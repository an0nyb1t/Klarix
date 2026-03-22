<div align="center">

# GitChat

**Chat with any GitHub repository.**

Paste a repo URL — GitChat ingests the code, commits, issues, and PRs,
then lets you ask questions, explore the codebase, and generate patches.

All from a single self-hosted app. No data leaves your machine.

---

Built for **software developers** and **security researchers**.

[Quick Start](#quick-start) &middot; [Features](#features) &middot; [Configuration](#configuration) &middot; [Tech Stack](#tech-stack)

</div>

---

## Features

| | |
|---|---|
| **Full Repo Ingestion** | Code files, commit history, branches, issues, pull requests — everything indexed and searchable |
| **RAG-Powered Chat** | Answers grounded in actual repo content with source citations, not hallucinations |
| **Diff Generation** | Ask for code changes and get `git apply`-compatible patches you can download or apply directly |
| **Apply Patch** | One-click patch application from the chat UI — changes committed to a local working clone |
| **Multi-Provider LLM** | Anthropic, OpenAI, Ollama, Claude Code CLI, or any OpenAI-compatible endpoint |
| **Local Embeddings** | Sentence-transformers runs on your machine — your code stays private |
| **Checkpoint & Resume** | Long ingestions survive interruptions and pick up exactly where they left off |
| **Rate Limit Awareness** | Auto-pause and resume when GitHub or LLM rate limits are hit |

## Quick Start

### Docker

```bash
git clone https://github.com/AnonyBit981/GitChat.git && cd GitChat
cp .env.example .env        # add your API keys
docker compose up --build
```

Open **http://localhost:8000** — paste a GitHub repo URL and start chatting.

### Local Development

**Prerequisites:** Python 3.11+ &middot; Node.js 18+ &middot; Git

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install && npm run dev
```

> The first run downloads the embedding model (~90 MB). Subsequent starts are instant.

Dev server runs at **http://localhost:5173** with API proxy to the backend.

### Production Build

```bash
cd frontend && npm run build && cd ..
cd backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

The backend serves the built frontend from `frontend/dist/`.

---

## Configuration

All settings can be configured via `.env` **or the in-app Settings UI** (gear icon) — no file editing required.

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub PAT for private repos (raises rate limit to 5K/hr) |
| `LLM_PROVIDER` | — | LLM provider to use |
| `LLM_API_KEY` | — | API key for your chosen provider |
| `LLM_MODEL` | — | Model identifier |
| `LLM_BASE_URL` | — | Endpoint URL for self-hosted providers |
| `LLM_RATE_LIMIT_TPM` | `0` | Tokens-per-minute cap (0 = unlimited) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Local embedding model |

Supports cloud providers, local models, and CLI-based integrations. See `.env.example` for details.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 &middot; TypeScript &middot; Tailwind CSS &middot; Vite |
| Backend | Python 3.11+ &middot; FastAPI &middot; Async SQLAlchemy |
| Vector DB | ChromaDB (local, file-based) |
| Database | SQLite (zero config) |
| Git | GitPython &middot; dual-clone architecture (mirror + working) |
| GitHub API | PyGithub |
| LLM | LiteLLM (multi-provider) |
| Embeddings | sentence-transformers (local) |

---

<div align="center">

API docs at **http://localhost:8000/docs**

**MIT License**

</div>
