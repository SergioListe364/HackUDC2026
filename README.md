# 🧠 BrAIner — AI-Powered Second Brain

> HackUDC 2026 · Team BrAIner

BrAIner is an intelligent note-taking assistant that automatically organizes your ideas, sets reminders, and generates structured summaries using local LLMs (via Ollama + Llama).

---

## ✨ Features

- 📝 **Smart note capture** — Write notes in natural language; the AI classifies and organizes them automatically into groups and subgroups
- 🗑️ **Natural deletion** — Say "remove X from the list" and the AI handles it
- ⏰ **Reminders** — "Remind me to go shopping at 8:30" creates a scheduled notification with email delivery
- 🎙️ **Voice input** — Record audio notes that are transcribed (Whisper) and classified automatically
- 📄 **Document extraction** — Upload `.txt`, `.pdf` or `.docx` files and extract structured ideas
- 📊 **Smart summaries** — Press PROCESS to get AI-generated summaries and key action points per group
- 🌐 **URL enrichment** — Paste a URL and the AI fetches title + description to enrich the note
- 🔍 **Search** — Full-text search across all your notes

---

## 🏗️ Architecture

```
Browser (Frontend)
      │  HTTP
      ▼
Backend API  (FastAPI · port 8000)
      │  HTTP
      ▼
AI Service   (FastAPI · port 8001)
      │  HTTP
      ▼
Ollama       (LLM inference · port 11434)
```

| Service | Tech | Port |
|---------|------|------|
| Frontend | Node.js / Vanilla JS | 5002 |
| Backend API | Python · FastAPI · SQLite | 8000 |
| AI Service | Python · FastAPI · Ollama | 8001 |
| Ollama | Local LLM (Llama 3.2) | 11434 |

---

## 🚀 Quick Start

### Prerequisites

- [Docker & Docker Compose](https://docs.docker.com/get-docker/)
- [Ollama](https://ollama.com/download) installed and running
- Llama model pulled:

```bash
ollama pull llama3.2
ollama serve
```

### Run with Docker Compose

```bash
git clone <repo-url>
cd HackUDC2026
docker-compose up --build
```

| URL | Description |
|-----|-------------|
| http://localhost:5002 | Frontend UI |
| http://localhost:8000/docs | Backend API docs |
| http://localhost:8001/docs | AI Service API docs |

---

## 🛠️ Local Development (without Docker)

### Backend

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### AI Service

```bash
cd ai-service
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### Frontend

```bash
cd frontend
npm install
node app
```

---

## 🗂️ Project Structure

```
BrAIner/
├── app/                  # Backend FastAPI service
│   ├── main.py           # API endpoints
│   ├── models.py         # SQLAlchemy models
│   ├── schemas.py        # Pydantic schemas
│   ├── ai_bridge.py      # Bridge to AI service
│   ├── classifier.py     # Rule-based pre-classifier
│   └── exporter.py       # Markdown / Obsidian export
├── ai-service/           # AI microservice
│   ├── main.py           # FastAPI app + endpoints
│   ├── classifier.py     # LLM classification logic
│   ├── processor.py      # PROCESS button logic
│   ├── transcriber.py    # Whisper transcription
│   ├── llm_client.py     # Ollama HTTP client
│   └── models.py         # Pydantic models
├── frontend/             # Web UI (Node.js + Vanilla JS)
├── data/                 # SQLite DB + Obsidian vault
├── docker-compose.yml
└── Dockerfile
```

---

## ⚙️ Configuration

Key environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_SERVICE_URL` | `http://ai-service:8001` | AI service URL |
| `NOTIFY_EMAIL` | — | Email for reminder notifications |
| `SMTP_HOST` | `localhost` | SMTP server host |
| `SMTP_PORT` | `1025` | SMTP server port |

---

## 📄 License

MIT © 2026 BrAIner Team
