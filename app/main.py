from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import os
import httpx
import smtplib
import logging
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler

from app.database import Base, engine, get_db
from app.models import InboxEntry, GroupSummary, Reminder
from app.schemas import EntryCreate, EntryUpdate, EntryOut
from app.classifier import classify
from app.exporter import export_to_markdown
from app.ai_bridge import classify_with_ai, ai_result_to_entry_fields, find_entry_to_delete, delete_entries_matching, request_summary
from pydantic import BaseModel
import re as _re

_CMD_VERBS = _re.compile(
    r'^(a[ñn]ade|agrega|crea|abre|a[ñn]adir|agregar|crear|abrir|pon|poner|mete|meter)\b',
    _re.IGNORECASE | _re.UNICODE,
)
# ── Auto-recordatorio por referencia horaria ──────────────────────────────────

_TIME_IN_NOTE_RE = _re.compile(
    r'\ba\s+las?\s+(\d{1,2})(?:[:.h](\d{2}))?\s*(?:h(?:oras?)?)?\b',
    _re.IGNORECASE,
)

_WEEKDAYS_BACKEND = {
    'lunes': 0, 'martes': 1, 'miercoles': 2, 'mi\xe9rcoles': 2,
    'jueves': 3, 'viernes': 4, 'sabado': 5, 's\xe1bado': 5, 'domingo': 6,
}

def _auto_fire_at(note_text: str) -> "datetime | None":
    """Extrae fire_at de un texto que contiene hora pero no palabra clave de recordatorio."""
    m = _TIME_IN_NOTE_RE.search(note_text.lower())
    if not m:
        return None
    hour   = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    now    = datetime.now()
    text_l = note_text.lower()

    day_offset = None
    if 'ma\xf1ana' in text_l or 'manana' in text_l:
        day_offset = 1
    elif 'pasado ma\xf1ana' in text_l or 'pasado manana' in text_l:
        day_offset = 2
    else:
        for wday_name, wday_num in _WEEKDAYS_BACKEND.items():
            if wday_name in text_l:
                diff = (wday_num - now.weekday()) % 7
                day_offset = diff if diff > 0 else 7
                break

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day_offset is not None:
        target += timedelta(days=day_offset)
    elif target <= now:
        target += timedelta(days=1)
    return target


def _maybe_auto_reminder(note_content: str, add_results: list, db: Session) -> "NoteOut | None":
    """Si la nota tiene referencia horaria y ning\xfan resultado es remind, crea recordatorio."""
    fire_at = _auto_fire_at(note_content)
    if not fire_at:
        return None
    # Busca el primer resultado "add" con idea y entry vinculada
    linked = next((r for r in add_results if r.action == "add" and r.idea and r.entry), None)
    message  = linked.idea  if linked else next((r.idea  for r in add_results if r.action == "add" and r.idea),  None)
    entry_id = linked.entry.id if linked else None
    if not message:
        return None
    db.add(Reminder(message=message, fire_at=fire_at, entry_id=entry_id))
    db.commit()
    return NoteOut(action="remind", group="recordatorios", idea=message, remind_at=fire_at.isoformat())
def _normalize(s: str) -> str:
    return _re.sub(r'\s+', ' ', s.lower().strip())

_TIME_FIX_RE = _re.compile(r'\b(\d{1,2}) (\d{2})\b')

def _fix_time_colons(text: str) -> str:
    """Restaura 'HH MM' -> 'HH:MM' cuando el LLM elimina los dos puntos de una hora."""
    def _repl(m):
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{m.group(1)}:{m.group(2)}"
        return m.group(0)
    return _TIME_FIX_RE.sub(_repl, text)

def _similar(a: str, b: str) -> bool:
    a, b = _normalize(a), _normalize(b)
    return a == b or (len(a) > 3 and (a in b or b in a))


# ── Auto-resumen ──────────────────────────────────────────────────────────────

def _get_group_ideas(group: str, subgroup: Optional[str], db: Session) -> list[str]:
    """Devuelve todas las ideas procesadas de un grupo/subgrupo."""
    all_entries = db.query(InboxEntry).filter(InboxEntry.status == "processed").all()
    ideas: list[str] = []
    for e in all_entries:
        parts = [t.strip() for t in (e.tags or "").split(",") if t.strip()]
        g  = parts[0] if parts else ""
        sg = parts[1] if len(parts) > 1 else None
        if g == group and sg == subgroup and e.summary:
            ideas.append(e.summary)
    return ideas


def _maybe_auto_summarize(group: str, subgroup: Optional[str], db: Session) -> None:
    """Si el grupo/subgrupo tiene >10 ideas, genera (o actualiza) su resumen."""
    ideas = _get_group_ideas(group, subgroup, db)
    if len(ideas) <= 10:
        return
    text = request_summary(group, subgroup, ideas)
    if not text:
        return
    existing = db.query(GroupSummary).filter(
        GroupSummary.group_name    == group,
        GroupSummary.subgroup_name == subgroup,
    ).first()
    if existing:
        existing.summary    = text
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(GroupSummary(group_name=group, subgroup_name=subgroup, summary=text))
    db.commit()

# Crear tablas si no existen (sin borrar datos existentes)
Base.metadata.create_all(bind=engine)

# ── Email + scheduler (recordatorios) ────────────────────────────────────────────
SMTP_HOST    = os.getenv("SMTP_HOST",    "mailhog")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "1025"))
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "usuario@hackudc.local")
_log = logging.getLogger("uvicorn.error")

def _send_email_notification(message: str, fire_at: datetime) -> None:
    when = fire_at.strftime("%A %d/%m/%Y a las %H:%M")
    body = f"¡Hora de actuar!\n\n⏰ {message}\n\nProgramado para: {when}\n\n— Digital Brain 🧠"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"⏰ Recordatorio: {message}"
    msg["From"]    = "brain@hackudc.local"
    msg["To"]      = NOTIFY_EMAIL
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as s:
            s.sendmail("brain@hackudc.local", [NOTIFY_EMAIL], msg.as_string())
        _log.info(f"[reminders] ✉️  Email enviado: {message}")
    except Exception as exc:
        _log.warning(f"[reminders] Email fallido: {exc}")


def _check_reminders() -> None:
    """Job del scheduler: dispara emails de recordatorios vencidos."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        now = datetime.now()
        due = db.query(Reminder).filter(
            Reminder.sent   == False,  # noqa: E712
            Reminder.fire_at <= now,
        ).all()
        for r in due:
            _send_email_notification(r.message, r.fire_at)
            r.sent = True
            # Al disparar el recordatorio, eliminar también la burbuja vinculada
            if r.entry_id:
                entry = db.query(InboxEntry).filter(InboxEntry.id == r.entry_id).first()
                if entry:
                    db.delete(entry)
        if due:
            db.commit()
    finally:
        db.close()


_scheduler = BackgroundScheduler()
_scheduler.add_job(_check_reminders, "interval", seconds=30, id="check_reminders")

app = FastAPI(title="Digital Brain API", version="0.1.0")

@app.on_event("startup")
def _startup():
    _scheduler.start()
    _log.info("[reminders] Scheduler arrancado — comprobando cada 30 s")

@app.on_event("shutdown")
def _shutdown():
    _scheduler.shutdown(wait=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas extra para el endpoint unificado ─────────────────────────────────

class NoteIn(BaseModel):
    content: str
    origin: Optional[str] = "manual"
    lang: Optional[str] = "es"

class NoteOut(BaseModel):
    action:        str          # "add" | "delete" | "ignored" | "remind"
    entry:         Optional[EntryOut] = None
    group:         Optional[str] = None
    subgroup:      Optional[str] = None
    idea:          Optional[str] = None
    ai_skipped:    bool = False
    deleted_count: int  = 0
    remind_at:     Optional[str] = None  # ISO datetime del recordatorio


class BatchSaveItem(BaseModel):
    idea:     str
    group:    str
    subgroup: Optional[str] = None

class BatchSaveIn(BaseModel):
    items:  list[BatchSaveItem]
    origin: str = "document"


# --- NOTA UNIFICADA (IA + BD en un solo paso) --------------------------------

def _process_single_ai(ai: dict, note: "NoteIn", db: Session) -> "NoteOut":
    """Procesa un resultado individual de la IA y lo guarda en BD."""
    if not ai.get("makes_sense", True):
        return NoteOut(action="ignored", ai_skipped=False)

    action = ai.get("action", "add")

    if action == "delete":
        deleted = delete_entries_matching(ai, db)
        first   = deleted[0] if deleted else None
        if first:
            db.refresh(first)
        return NoteOut(
            action="delete", entry=first,
            group=ai.get("group"), subgroup=ai.get("subgroup"), idea=ai.get("idea"),
            deleted_count=len(deleted),
        )

    if action == "remind":
        remind_at_str = ai.get("remind_at")
        try:
            fire_at = datetime.fromisoformat(remind_at_str) if remind_at_str else datetime.now() + timedelta(minutes=5)
        except (ValueError, TypeError):
            fire_at = datetime.now() + timedelta(minutes=5)
        message = ai.get("idea") or note.content
        reminder = Reminder(message=message, fire_at=fire_at)
        db.add(reminder)
        db.commit()
        return NoteOut(
            action="remind",
            group="recordatorios",
            idea=message,
            remind_at=fire_at.isoformat(),
        )

    fields     = ai_result_to_entry_fields(ai, note.content)
    summary    = _fix_time_colons(fields.get("summary", "") or "")
    entry_type = classify(note.content)
    tags       = fields.get("tags", "")
    # Preservar URL: la IA la detecta y devuelve en ai["url"]; fallback a regex
    source_url = fields.get("source_url") or None
    if not source_url:
        _url_m = _re.search(r'https?://\S+', note.content)
        source_url = _url_m.group(0).rstrip('.,)>') if _url_m else None

    if summary and _normalize(summary) == _normalize(note.content):
        summary = ""
    if summary and _CMD_VERBS.match(summary.strip()):
        summary = ""

    # Cada idea distinta se guarda con su propio texto para evitar colisiones
    # de UNIQUE(content) cuando la nota produce múltiples resultados.
    content_to_store = summary if summary else note.content

    existing_dup = (
        db.query(InboxEntry)
        .filter(InboxEntry.status == "processed", InboxEntry.tags == tags)
        .all()
    )
    for dup in existing_dup:
        if _similar(dup.summary or "", summary):
            return NoteOut(action="add", entry=dup,
                           group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)

    db_entry = InboxEntry(content=content_to_store, origin=note.origin,
                          type=entry_type, summary=summary, tags=tags,
                          source_url=source_url)
    db.add(db_entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(InboxEntry).filter(InboxEntry.content == content_to_store).first()
        if existing:
            return NoteOut(action="add", entry=existing,
                           group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)
        raise HTTPException(status_code=409, detail="Entry already exists")
    db.refresh(db_entry)

    try:
        destination = export_to_markdown(db_entry)
        db_entry.destination  = destination
        db_entry.status       = "processed"
        db_entry.processed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(db_entry)
    except Exception:
        pass

    if ai.get("group"):
        try:
            _maybe_auto_summarize(ai["group"], ai.get("subgroup"), db)
        except Exception:
            pass  # no interrumpir el flujo principal

    return NoteOut(action="add", entry=db_entry,
                   group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)


@app.post("/batch-save", status_code=201)
def batch_save(batch: "BatchSaveIn", db: Session = Depends(get_db)):
    """
    Guarda una lista de ideas pre-clasificadas directamente en BD sin pasar por IA.
    Usado por la función de importación de documentos del frontend.
    """
    saved = 0
    for item in batch.items:
        tags = f"{item.group},{item.subgroup}" if item.subgroup else item.group
        existing_dup = (
            db.query(InboxEntry)
            .filter(InboxEntry.status == "processed", InboxEntry.tags == tags)
            .all()
        )
        if any(_similar(dup.summary or "", item.idea) for dup in existing_dup):
            continue
        db_entry = InboxEntry(
            content=item.idea,
            origin=batch.origin,
            type="note",
            summary=item.idea,
            tags=tags,
            status="processed",
            processed_at=datetime.now(timezone.utc),
        )
        db.add(db_entry)
        try:
            db.commit()
            saved += 1
        except IntegrityError:
            db.rollback()
    return {"saved": saved, "total": len(batch.items)}


@app.post("/note", response_model=list[NoteOut], status_code=201)
def add_note(note: NoteIn, db: Session = Depends(get_db)):
    """
    Endpoint principal. Devuelve una LISTA de resultados (normalmente 1,
    varios cuando la nota contiene múltiples ideas distintas).
    """
    ai_list = classify_with_ai(note.content, db, lang=note.lang or "es")

    if ai_list is None:
        entry_type = classify(note.content)
        db_entry = InboxEntry(content=note.content, origin=note.origin, type=entry_type)
        db.add(db_entry)
        db.commit()
        db.refresh(db_entry)
        return [NoteOut(action="add", entry=db_entry, ai_skipped=True)]

    results = [_process_single_ai(ai, note, db) for ai in ai_list]

    # Auto-recordatorio: si la nota tiene hora ("a las HH:MM") y la IA no
    # gener\xf3 ya un recordatorio, creamos uno autom\xe1ticamente.
    has_add    = any(r.action == "add"    for r in results)
    has_remind = any(r.action == "remind" for r in results)
    if has_add and not has_remind:
        auto = _maybe_auto_reminder(note.content, results, db)
        if auto:
            results.append(auto)

    return results


# --- INBOX ---

@app.post("/inbox", response_model=EntryOut, status_code=201)
def create_entry(entry: EntryCreate, db: Session = Depends(get_db)):
    existing = db.query(InboxEntry).filter(InboxEntry.content == entry.content).first()
    if existing:
        raise HTTPException(status_code=409, detail="Entry with same content already exists")

    entry_type = classify(entry.content)
    db_entry = InboxEntry(**entry.dict(), type=entry_type)
    db.add(db_entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Entry with same content already exists")

    db.refresh(db_entry)
    return db_entry


@app.get("/inbox", response_model=List[EntryOut])
def list_inbox(status: str = "pending", db: Session = Depends(get_db)):
    return db.query(InboxEntry).filter(InboxEntry.status == status).all()


@app.get("/inbox/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


@app.patch("/inbox/{entry_id}", response_model=EntryOut)
def update_entry(entry_id: int, data: EntryUpdate, db: Session = Depends(get_db)):
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    for field, value in data.dict(exclude_none=True).items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


# --- PROCESADO: valida la propuesta de la IA y exporta ---

@app.post("/inbox/{entry_id}/process", response_model=EntryOut)
def process_entry(entry_id: int, db: Session = Depends(get_db)):
    """
    Tus compis llamarán a este endpoint DESPUÉS de que la IA haya
    rellenado 'summary' y 'tags' vía PATCH. Este endpoint exporta
    a Markdown, hace commit y marca como processed.
    """
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status == "processed":
        raise HTTPException(status_code=400, detail="Already processed")

    destination = export_to_markdown(entry)
    entry.destination   = destination
    entry.status        = "processed"
    entry.processed_at  = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/inbox/{entry_id}", status_code=204)
def discard_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.status = "discarded"
    db.commit()


@app.delete("/groups/{group_name}", status_code=204)
def delete_group(group_name: str, db: Session = Depends(get_db)):
    """Marca como descartadas todas las entradas cuyo primer tag coincide con group_name."""
    entries = db.query(InboxEntry).filter(InboxEntry.status == "processed").all()
    for entry in entries:
        parts = [t.strip() for t in (entry.tags or "").split(",") if t.strip()]
        if parts and parts[0] == group_name:
            entry.status = "discarded"
    db.commit()


class GroupRename(BaseModel):
    new_name: str


@app.patch("/groups/{group_name}", status_code=200)
def rename_group(group_name: str, body: GroupRename, db: Session = Depends(get_db)):
    """Renombra un grupo actualizando los tags de todas sus entradas."""
    entries = db.query(InboxEntry).filter(InboxEntry.status == "processed").all()
    for entry in entries:
        parts = [t.strip() for t in (entry.tags or "").split(",") if t.strip()]
        if parts and parts[0] == group_name:
            parts[0] = body.new_name
            entry.tags = ", ".join(parts)
    db.commit()
    return {"renamed": group_name, "new_name": body.new_name}


@app.patch("/groups/{group_name}/subgroups/{subgroup_name}", status_code=200)
def rename_subgroup(group_name: str, subgroup_name: str, body: GroupRename, db: Session = Depends(get_db)):
    """Renombra un subgrupo actualizando los tags de todas sus entradas."""
    entries = db.query(InboxEntry).filter(InboxEntry.status == "processed").all()
    for entry in entries:
        parts = [t.strip() for t in (entry.tags or "").split(",") if t.strip()]
        if len(parts) >= 2 and parts[0] == group_name and parts[1] == subgroup_name:
            parts[1] = body.new_name
            entry.tags = ", ".join(parts)
    db.commit()
    return {"renamed": subgroup_name, "new_name": body.new_name}


@app.delete("/groups/{group_name}/subgroups/{subgroup_name}", status_code=204)
def delete_subgroup(group_name: str, subgroup_name: str, db: Session = Depends(get_db)):
    """Descarta todas las entradas de un subgrupo."""
    entries = db.query(InboxEntry).filter(InboxEntry.status == "processed").all()
    for entry in entries:
        parts = [t.strip() for t in (entry.tags or "").split(",") if t.strip()]
        if len(parts) >= 2 and parts[0] == group_name and parts[1] == subgroup_name:
            entry.status = "discarded"
    db.commit()


@app.post("/inbox/{entry_id}/ai-classify", response_model=EntryOut)
def ai_classify_entry(entry_id: int, db: Session = Depends(get_db)):
    """
    Clasifica con IA una entrada ya existente (pending) y rellena
    summary + tags.  No exporta a Markdown (usa /process para eso).
    """
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    ai_list = classify_with_ai(entry.content, db)
    if ai_list is None:
        raise HTTPException(status_code=503, detail="Servicio de IA no disponible")
    ai = ai_list[0]
    if not ai.get("makes_sense", True):
        raise HTTPException(status_code=422, detail=ai.get("reason", "La nota no tiene sentido"))

    fields = ai_result_to_entry_fields(ai, entry.content)
    entry.summary = fields.get("summary", "")
    entry.tags    = fields.get("tags", "")
    db.commit()
    db.refresh(entry)
    return entry


# --- BÚSQUEDA básica (tus compis amplían con ChromaDB) ---

@app.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    results = db.query(InboxEntry).filter(
        InboxEntry.content.contains(q) |
        InboxEntry.tags.contains(q) |
        InboxEntry.summary.contains(q)
    ).all()
    return results


# --- SUMMARIES ---

@app.get("/summaries")
def get_summaries(db: Session = Depends(get_db)):
    """Devuelve todos los resúmenes automáticos de grupos/subgrupos."""
    return [
        {"group": s.group_name, "subgroup": s.subgroup_name, "summary": s.summary}
        for s in db.query(GroupSummary).all()
    ]


# ── Audio: proxy hacia el servicio de IA ──────────────────────────────────────

_AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8001")

@app.post("/transcribe")
async def transcribe_proxy(audio: UploadFile = File(...)):
    """
    Recibe un fichero de audio del frontend y lo reenvía al servicio de IA
    para transcribirlo con Whisper. Devuelve {"transcribed_text": "..."}.
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="El fichero de audio está vacío.")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_AI_SERVICE_URL}/transcribe",
                files={"audio": (audio.filename or "recording.webm", audio_bytes, audio.content_type or "audio/webm")},
            )
        if resp.status_code == 503:
            raise HTTPException(status_code=503, detail="Whisper no disponible en el servicio de IA.")
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Servicio de IA no disponible.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Recordatorios ─────────────────────────────────────────────────────────────

@app.get("/reminders")
def list_reminders(sent: Optional[bool] = None, db: Session = Depends(get_db)):
    """Lista todos los recordatorios. Filtra por ?sent=false para ver los pendientes."""
    q = db.query(Reminder)
    if sent is not None:
        q = q.filter(Reminder.sent == sent)
    return [
        {
            "id":         r.id,
            "message":    r.message,
            "fire_at":    r.fire_at.isoformat(),
            "sent":       r.sent,
            "created_at": r.created_at.isoformat(),
            "entry_id":   r.entry_id,
        }
        for r in q.order_by(Reminder.fire_at).all()
    ]


@app.delete("/reminders/{reminder_id}", status_code=204)
def delete_reminder(reminder_id: int, db: Session = Depends(get_db)):
    """Elimina un recordatorio por ID y también la burbuja vinculada (si existe)."""
    r = db.query(Reminder).filter(Reminder.id == reminder_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Recordatorio no encontrado")
    if r.entry_id:
        entry = db.query(InboxEntry).filter(InboxEntry.id == r.entry_id).first()
        if entry:
            db.delete(entry)
    db.delete(r)
    db.commit()
