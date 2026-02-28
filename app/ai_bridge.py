"""
Puente entre el backend FastAPI y el servicio de IA (ai-service).

Funciones exportadas:
  classify_with_ai          → Llama a POST /classify y devuelve lista de dicts
  ai_result_to_entry_fields → Convierte un resultado de la IA a campos de BD
  find_entry_to_delete      → Busca la entrada que la IA quiere eliminar
  delete_entries_matching   → Elimina las entradas que coincidan con la IA
  request_summary           → Llama a POST /summarize y devuelve el texto
"""

from __future__ import annotations

import logging
import os
import re

import httpx
from sqlalchemy.orm import Session

from app.models import InboxEntry

log = logging.getLogger("uvicorn.error")

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://ai-service:8001")

# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def _similar(a: str, b: str) -> bool:
    a, b = _normalize(a), _normalize(b)
    return a == b or (len(a) > 3 and (a in b or b in a))


def _build_existing_groups(db: Session) -> list[dict]:
    """Construye la lista de grupos/subgrupos existentes desde la BD."""
    entries = (
        db.query(InboxEntry)
        .filter(InboxEntry.status == "processed", InboxEntry.tags.isnot(None))
        .all()
    )

    groups: dict[str, dict] = {}  # name → {name, ideas, subgroups}

    for e in entries:
        parts = [t.strip() for t in (e.tags or "").split(",") if t.strip()]
        if not parts:
            continue
        group_name = parts[0]
        subgroup_name = parts[1] if len(parts) > 1 else None
        idea = e.summary or e.content or ""

        if group_name not in groups:
            groups[group_name] = {"name": group_name, "ideas": [], "subgroups": []}
        g = groups[group_name]

        if subgroup_name:
            sg = next((s for s in g["subgroups"] if s["name"] == subgroup_name), None)
            if sg is None:
                sg = {"name": subgroup_name, "ideas": []}
                g["subgroups"].append(sg)
            if idea and idea not in sg["ideas"]:
                sg["ideas"].append(idea)
        else:
            if idea and idea not in g["ideas"]:
                g["ideas"].append(idea)

    return list(groups.values())


# ── classify_with_ai ──────────────────────────────────────────────────────────

def classify_with_ai(content: str, db: Session, lang: str = "es") -> list[dict] | None:
    """
    Llama al servicio de IA para clasificar una nota.

    Devuelve una lista de dicts con los campos de ClassificationResult,
    o None si el servicio de IA no está disponible.
    """
    existing_groups = _build_existing_groups(db)
    payload = {
        "text": content,
        "existing_groups": existing_groups,
        "lang": lang,
    }

    try:
        with httpx.Client(timeout=90) as client:
            resp = client.post(f"{AI_SERVICE_URL}/classify", json=payload)
            resp.raise_for_status()
            data = resp.json()
            # data es list[ClassificationResult] serializado
            if isinstance(data, list):
                return data
            # Por si acaso el servicio devuelve un solo objeto
            return [data]
    except Exception as exc:
        log.warning(f"[ai_bridge] classify_with_ai falló: {exc}")
        return None


# ── ai_result_to_entry_fields ─────────────────────────────────────────────────

def ai_result_to_entry_fields(ai: dict, content: str) -> dict:
    """
    Convierte un resultado de la IA a los campos que necesita InboxEntry.

    Devuelve un dict con:
      summary    – la idea limpia que propuso la IA (o vacío)
      tags       – "grupo" o "grupo,subgrupo"
      source_url – URL detectada por la IA (o None)
    """
    idea     = ai.get("idea") or ""
    group    = ai.get("group") or ""
    subgroup = ai.get("subgroup") or ""

    tags = group
    if subgroup:
        tags = f"{group},{subgroup}"

    return {
        "summary":    idea,
        "tags":       tags,
        "source_url": ai.get("url") or None,
    }


# ── find_entry_to_delete / delete_entries_matching ────────────────────────────

def _entries_matching_delete(ai: dict, db: Session) -> list[InboxEntry]:
    """Devuelve las entradas de BD que coinciden con el intent de borrado de la IA."""
    idea     = _normalize(ai.get("idea") or "")
    group    = _normalize(ai.get("group") or "")
    subgroup = _normalize(ai.get("subgroup") or "")

    candidates = (
        db.query(InboxEntry)
        .filter(InboxEntry.status == "processed")
        .all()
    )

    matches: list[InboxEntry] = []
    for e in candidates:
        parts         = [t.strip() for t in (e.tags or "").split(",") if t.strip()]
        entry_group   = _normalize(parts[0]) if parts else ""
        entry_subgroup = _normalize(parts[1]) if len(parts) > 1 else ""
        entry_idea    = _normalize(e.summary or e.content or "")

        group_ok    = not group    or _similar(entry_group,    group)
        subgroup_ok = not subgroup or _similar(entry_subgroup, subgroup)
        idea_ok     = not idea     or _similar(entry_idea,     idea)

        if group_ok and subgroup_ok and idea_ok:
            matches.append(e)

    return matches


def find_entry_to_delete(ai: dict, db: Session) -> InboxEntry | None:
    """Devuelve la primera entrada que la IA quiere eliminar (o None)."""
    matches = _entries_matching_delete(ai, db)
    return matches[0] if matches else None


def delete_entries_matching(ai: dict, db: Session) -> list[InboxEntry]:
    """Elimina de la BD las entradas que coincidan con el intent de la IA y las devuelve."""
    matches = _entries_matching_delete(ai, db)
    for entry in matches:
        db.delete(entry)
    if matches:
        db.commit()
    return matches


# ── request_summary ───────────────────────────────────────────────────────────

def request_summary(group: str, subgroup: str | None, ideas: list[str]) -> str:
    """
    Llama al servicio de IA para generar un resumen del grupo/subgrupo.

    Devuelve el texto del resumen, o cadena vacía si falla.
    """
    payload = {
        "group":   group,
        "subgroup": subgroup,
        "ideas":   ideas,
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{AI_SERVICE_URL}/summarize", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("summary", "")
    except Exception as exc:
        log.warning(f"[ai_bridge] request_summary falló: {exc}")
        return ""
