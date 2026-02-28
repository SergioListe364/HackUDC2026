"""
Servicio de IA para organización de notas — HackUDC 2026
=========================================================
Endpoints:
  POST /classify        → Clasifica una nota (texto) en grupo + subgrupo + idea
  POST /transcribe      → Transcribe un audio a texto (Whisper)
  POST /classify-audio  → Transcribe un audio y lo clasifica directamente
  POST /process         → Procesa todos los proyectos (botón PROCESAR)
  POST /translate       → Traduce una lista de textos al idioma indicado
  GET  /health          → Estado del servicio, Ollama y Whisper
  GET  /models          → Modelos disponibles en Ollama
"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from models import (
    AudioClassificationResult,
    NoteRequest,
    ProcessRequest,
    ClassificationResult,
    ProcessResult,
    TranscriptionResult,
    ErrorResponse,
    SummarizeRequest,
    SummarizeResult,
)
from pydantic import BaseModel
from classifier import classify_note
from processor import process_projects, summarize_ideas
from transcriber import is_whisper_available, transcribe_audio
from llm_client import is_ollama_running, get_available_models, MODEL_NAME, _call_ollama

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if is_ollama_running():
        models = get_available_models()
        log.info(f"✅  Ollama activo. Modelos disponibles: {models}")
        if not any(MODEL_NAME in m for m in models):
            log.warning(
                f"⚠️  El modelo '{MODEL_NAME}' no está descargado. "
                f"Ejecuta: ollama pull {MODEL_NAME}"
            )
    else:
        log.warning("⚠️  Ollama NO está corriendo. Inicia Ollama antes de usar los endpoints.")
    yield


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="HackUDC — AI Notes Organizer",
    description="Servicio de IA que clasifica notas en proyectos y genera resúmenes.",
    version="1.0.0",
    lifespan=lifespan,
)

# Permitir peticiones desde cualquier origen (para que el frontend/backend puedan llamarnos)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Sistema"])
def health():
    """
    Estado del servicio. Comprueba si Ollama está activo y qué modelos hay.

    Respuesta de ejemplo:
    ```json
    {
      "status": "ok",
      "ollama": true,
      "model": "llama3.2",
      "available_models": ["llama3.2:latest"]
    }
    ```
    """
    ollama_ok  = is_ollama_running()
    whisper_ok = is_whisper_available()
    models = get_available_models() if ollama_ok else []
    return {
        "status":           "ok" if ollama_ok else "ollama_unavailable",
        "ollama":           ollama_ok,
        "model":            MODEL_NAME,
        "available_models": models,
        "whisper":          whisper_ok,
    }


@app.get("/models", tags=["Sistema"])
def list_models():
    """Lista los modelos disponibles en Ollama."""
    if not is_ollama_running():
        raise HTTPException(status_code=503, detail="Ollama no está corriendo")
    return {"models": get_available_models()}


@app.post("/summarize", response_model=SummarizeResult, tags=["IA"])
def summarize_group(request: SummarizeRequest) -> SummarizeResult:
    """Genera un resumen de todas las ideas de un grupo/subgrupo."""
    if not is_ollama_running():
        raise HTTPException(status_code=503, detail="Ollama no est\u00e1 corriendo")
    summary = summarize_ideas(request.group, request.subgroup, request.ideas)
    return SummarizeResult(group=request.group, subgroup=request.subgroup, summary=summary)


@app.post(
    "/classify",
    response_model=list[ClassificationResult],
    responses={503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["IA"],
)
def classify(request: NoteRequest):
    """
    Clasifica una nota en texto libre y devuelve en qué grupo y sección guardarla.

    **Body de ejemplo:**
    ```json
    {
      "text": "una de las páginas web que quiero crear sería sobre gatos",
      "existing_groups": [
        {
          "name": "desarrollo pagina web",
          "ideas": ["crear página web", "fondo azul"],
          "subgroups": []
        }
      ]
    }
    ```

    **Respuesta de ejemplo:**
    ```json
    {
      "group": "desarrollo pagina web",
      "subgroup": "pagina sobre gatos",
      "idea": "página temática sobre gatos",
      "is_new_group": false,
      "is_new_subgroup": true,
      "inherit_parent_ideas": true
    }
    ```
    **Cuando `inherit_parent_ideas` es `true`**, el backend debe copiar las ideas del proyecto
    padre al nuevo subproyecto antes de guardar la idea nueva.
    """
    if not is_ollama_running():
        raise HTTPException(
            status_code=503,
            detail="Ollama no está corriendo. Inicia Ollama con 'ollama serve'.",
        )

    try:
        log.info(f"📝  Clasificando nota: '{request.text}'")
        results = classify_note(request.text, request.existing_groups, lang=request.lang or "es")
        for r in results:
            if r.makes_sense:
                idea_info = f", idea='{r.idea}'" if r.idea else " (sin idea)"
                log.info(
                    f"✅  → grupo='{r.group}', subgrupo='{r.subgroup}'"
                    f"{idea_info}"
                )
            else:
                log.info(f"🚫  Nota sin sentido: {r.reason}")
        return results
    except Exception as exc:
        log.error(f"❌  Error al clasificar: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/process",
    response_model=ProcessResult,
    responses={503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["IA"],
)
def process(request: ProcessRequest):
    """
    Procesa todos los proyectos y sus notas (botón PROCESAR).
    Genera resúmenes, puntos clave y un resumen global.

    **Body de ejemplo:**
    ```json
    {
      "projects": [
        {
          "name": "gimnasio",
          "sections": [
            {
              "name": "dia de espalda",
              "notes": ["hacer bíceps", "remo con barra", "jalón al pecho"]
            },
            {
              "name": "dia de pierna",
              "notes": ["sentadillas 4x8", "prensa", "extensiones de cuádriceps"]
            }
          ]
        }
      ]
    }
    ```

    **Respuesta de ejemplo:**
    ```json
    {
      "projects": [
        {
          "project_name": "gimnasio",
          "suggested_title": "Plan de Entrenamiento",
          "summary": "Tienes un plan de gimnasio bien estructurado...",
          "key_points": [
            {"text": "Hacer bíceps en el día de espalda", "category": "acción"},
            {"text": "Completar rutina de piernas con sentadillas", "category": "acción"}
          ]
        }
      ],
      "global_summary": "Estás construyendo una rutina de entrenamiento completa..."
    }
    ```
    """
    if not is_ollama_running():
        raise HTTPException(
            status_code=503,
            detail="Ollama no está corriendo. Inicia Ollama con 'ollama serve'.",
        )

    if not request.groups:
        raise HTTPException(status_code=400, detail="No hay grupos que procesar.")

    try:
        log.info(f"🔄  Procesando {len(request.groups)} grupo(s)...")
        result = process_projects(request.groups)
        log.info(f"✅  Procesado correctamente.")
        return result
    except Exception as exc:
        log.error(f"❌  Error al procesar: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Audio: transcripción y clasificación ─────────────────────────────────────

@app.post(
    "/transcribe",
    response_model=TranscriptionResult,
    responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["Audio"],
)
async def transcribe(audio: UploadFile = File(...)):
    """
    Transcribe un fichero de audio a texto usando Whisper.

    - Enviar como `multipart/form-data` con el campo `audio`.
    - Formatos soportados: **mp3, wav, m4a, ogg, webm, flac** (requiere ffmpeg).

    **Respuesta de ejemplo:**
    ```json
    { "transcribed_text": "me gustaría crear una página web" }
    ```
    """
    if not is_whisper_available():
        raise HTTPException(
            status_code=503,
            detail="faster-whisper no está instalado. Ejecuta: pip install faster-whisper",
        )
    try:
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(status_code=422, detail="El fichero de audio está vacío.")
        log.info(f"🎙️  Transcribiendo '{audio.filename}' ({len(audio_bytes)} bytes)...")
        text = transcribe_audio(audio_bytes, audio.filename or "audio.wav")
        if not text:
            raise HTTPException(status_code=422, detail="No se detectó habla en el audio.")
        return TranscriptionResult(transcribed_text=text)
    except HTTPException:
        raise
    except Exception as exc:
        log.error(f"❌  Error al transcribir: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/classify-audio",
    response_model=AudioClassificationResult,
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["Audio"],
)
async def classify_audio(
    audio: UploadFile = File(...),
    existing_groups: str = Form(default="[]"),
):
    """
    **Todo en uno**: transcribe el audio y clasifica el texto resultante.

    - Enviar como `multipart/form-data`:
      - `audio` → fichero de audio
      - `existing_groups` → JSON string con la lista de grupos actuales (opcional)

    **Ejemplo de `existing_groups`:**
    ```json
    [{"name": "desarrollo pagina web", "ideas": ["fondo azul"], "subgroups": []}]
    ```

    **Respuesta de ejemplo:**
    ```json
    {
      "transcribed_text": "quiero añadir una galería de fotos a la página de gatos",
      "classification": {
        "makes_sense": true,
        "group": "desarrollo pagina web",
        "subgroup": "pagina sobre gatos",
        "idea": "galería de fotos",
        "is_new_group": false,
        "is_new_subgroup": false,
        "inherit_parent_ideas": false
      }
    }
    ```
    """
    if not is_whisper_available():
        raise HTTPException(
            status_code=503,
            detail="faster-whisper no está instalado. Ejecuta: pip install faster-whisper",
        )
    if not is_ollama_running():
        raise HTTPException(
            status_code=503,
            detail="Ollama no está corriendo. Inicia Ollama con 'ollama serve'.",
        )

    # ── 1. Parsear existing_projects ─────────────────────────────────────────
    import json
    try:
        existing = json.loads(existing_groups)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="existing_groups no es JSON válido.")

    # ── 2. Transcribir ────────────────────────────────────────────────────────
    try:
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(status_code=422, detail="El fichero de audio está vacío.")
        log.info(f"🎙️  Transcribiendo '{audio.filename}' ({len(audio_bytes)} bytes)...")
        text = transcribe_audio(audio_bytes, audio.filename or "audio.wav")
        if not text:
            raise HTTPException(status_code=422, detail="No se detectó habla en el audio.")
    except HTTPException:
        raise
    except Exception as exc:
        log.error(f"❌  Error al transcribir: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error en transcripción: {exc}")

    # ── 3. Clasificar ─────────────────────────────────────────────────────────
    try:
        log.info(f"📝  Clasificando texto transcrito: '{text}'")
        result = classify_note(text, existing)
        if result.makes_sense:
            log.info(f"✅  → grupo='{result.group}', idea='{result.idea}'")
        else:
            log.info(f"🚫  Audio sin sentido clasificable: {result.reason}")
        return AudioClassificationResult(transcribed_text=text, classification=result)
    except Exception as exc:
        log.error(f"❌  Error al clasificar: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error en clasificación: {exc}")


# ── Extracción de documentos ──────────────────────────────────────────────────

def _extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """Extrae el texto plano de .txt, .pdf o .docx."""
    parts = (filename or "file.txt").lower().rsplit('.', 1)
    ext   = parts[-1] if len(parts) > 1 else 'txt'
    if ext == 'txt':
        return file_bytes.decode('utf-8', errors='replace')
    elif ext == 'pdf':
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        return '\n'.join(page.extract_text() or '' for page in reader.pages)
    elif ext in ('docx', 'doc'):
        import io
        from docx import Document as DocxDocument
        doc = DocxDocument(io.BytesIO(file_bytes))
        return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
    else:
        raise ValueError(f"Formato no soportado: .{ext}. Usa .txt, .pdf o .docx")


def _extract_ideas_from_document(text: str, lang: str = "es") -> list[dict]:
    """Divide el texto en trozos y pide al LLM que extraiga ideas estructuradas."""
    import json as _json
    CHUNK_SIZE = 8000
    OVERLAP    = 500
    results    = []

    if lang == "en":
        prompt_header = (
            "Read the following text excerpt and extract ALL important information as structured notes.\n"
            "Organize each idea into a 'group' (main topic) and optionally a 'subgroup' (sub-topic).\n"
            "Return ONLY a JSON array. Each element MUST have exactly these keys:\n"
            "  - \"group\":    short group name (2-5 words, no punctuation)\n"
            "  - \"subgroup\": short sub-topic name (2-5 words) or null if not applicable\n"
            "  - \"idea\":     concise statement of one concrete idea (1 sentence)\n"
            "Rules: only extract real information from the text, do not invent, do not repeat ideas.\n"
            "Use English for all group names, subgroup names and ideas.\n\n"
            "TEXT:\n"
        )
        system_msg = (
            "You are an expert information extractor. "
            "Output ONLY a valid JSON array of objects with keys group, subgroup, idea. "
            "All values must be in English. No markdown, no explanations, no extra text."
        )
    else:
        prompt_header = (
            "Lee el siguiente fragmento de texto y extrae TODA la información importante como notas estructuradas.\n"
            "Organiza cada idea en un 'group' (tema principal) y opcionalmente un 'subgroup' (subtema).\n"
            "Devuelve ÚNICAMENTE un array JSON. Cada elemento DEBE tener exactamente estas claves:\n"
            "  - \"group\":    nombre corto del grupo (2-5 palabras, sin puntuación)\n"
            "  - \"subgroup\": nombre corto del subtema (2-5 palabras) o null si no aplica\n"
            "  - \"idea\":     enunciado concreto de una idea (1 frase)\n"
            "Reglas: extrae solo información real del texto, no inventes, no repitas ideas.\n"
            "Usa español para todos los nombres de grupos, subgrupos e ideas.\n\n"
            "TEXTO:\n"
        )
        system_msg = (
            "Eres un extractor experto de información. "
            "Devuelve ÚNICAMENTE un array JSON válido con objetos que tengan las claves group, subgroup, idea. "
            "Todos los valores deben estar en español. Sin markdown, sin explicaciones, sin texto extra."
        )

    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i: i + CHUNK_SIZE])
        i += CHUNK_SIZE - OVERLAP

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        prompt = prompt_header + chunk
        raw = _call_ollama(
            prompt=prompt,
            system=system_msg,
            temperature=0.1,
        )
        try:
            start = raw.index('[')
            end   = raw.rindex(']') + 1
            items = _json.loads(raw[start:end])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get('idea'), str) and item['idea'].strip():
                        results.append({
                            'group':    str(item.get('group') or 'general').strip(),
                            'subgroup': str(item['subgroup']).strip() if item.get('subgroup') else None,
                            'idea':     item['idea'].strip(),
                        })
        except Exception:
            pass  # skip failed chunks

    # Deduplicate by idea text (case-insensitive)
    seen   = set()
    unique = []
    for r in results:
        key = r['idea'].lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


@app.post("/extract-document", tags=["Documentos"])
async def extract_document(file: UploadFile = File(...), lang: str = Form(default="es")):
    """
    Lee un documento completo (.txt, .pdf, .docx), extrae su contenido
    y devuelve ideas organizadas en grupos y subgrupos.

    **Enviar como** `multipart/form-data` con el campo `file`.

    **Respuesta de ejemplo:**
    ```json
    {
      "filename": "proyecto.txt",
      "total_chars": 2400,
      "extractions": [
        {"group": "Diseño web", "subgroup": "Paleta de colores", "idea": "Usar fondo azul marino"},
        {"group": "Diseño web", "subgroup": null, "idea": "El logo debe ser minimalista"}
      ]
    }
    ```
    """
    if not is_ollama_running():
        raise HTTPException(status_code=503, detail="Ollama no está corriendo.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="El archivo está vacío.")

    try:
        text = _extract_text_from_file(file_bytes, file.filename or "document.txt")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.error(f"❌  Error extrayendo texto: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"No se pudo leer el documento: {exc}")

    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="El documento no contiene texto legible.")

    log.info(f"📄  Procesando documento '{file.filename}' ({len(text)} chars, lang={lang})…")
    try:
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        extractions = await loop.run_in_executor(
            None, lambda: _extract_ideas_from_document(text, lang=lang)
        )
    except Exception as exc:
        log.error(f"❌  Error extrayendo ideas: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    log.info(f"✅  Extraídas {len(extractions)} ideas de '{file.filename}'")
    return {
        "filename":    file.filename,
        "total_chars": len(text),
        "extractions": extractions,
    }


# ── Traducción ────────────────────────────────────────────────────────────────

class TranslateRequest(BaseModel):
    texts: list[str]
    target_lang: str = "en"

class TranslateResponse(BaseModel):
    translations: list[str]

@app.post("/translate", response_model=TranslateResponse, tags=["IA"])
def translate_texts(request: TranslateRequest):
    """Traduce una lista de textos cortos al idioma indicado usando el LLM."""
    import json as _json
    if not request.texts:
        return TranslateResponse(translations=[])
    lang_name = "English" if request.target_lang.lower() == "en" else request.target_lang
    numbered  = "\n".join(f"{i+1}. {t}" for i, t in enumerate(request.texts))
    prompt = (
        f"Translate each item below to {lang_name}. "
        "Return ONLY a JSON array of translated strings in the same order. "
        "No explanations, no extra text.\n\n"
        f"{numbered}"
    )
    raw = _call_ollama(
        prompt=prompt,
        system="You are a professional translator. Output ONLY a valid JSON array of strings.",
        temperature=0.0,
    )
    try:
        start = raw.index('[')
        end   = raw.rindex(']') + 1
        result = _json.loads(raw[start:end])
        if isinstance(result, list) and len(result) == len(request.texts):
            return TranslateResponse(translations=[str(t) for t in result])
    except Exception:
        pass
    return TranslateResponse(translations=request.texts)


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
