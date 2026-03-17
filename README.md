# GitChat

Chat with any GitHub repository. Paste a repo URL, GitChat ingests the code, commits, issues, and PRs, then lets you ask questions and generate patches — all from a single self-hosted app.

Built for **software developers** and **security researchers**.

## Features

- **Full repo ingestion** — code files, commit history, branches, issues, pull requests
- **RAG-powered chat** — answers grounded in actual repo content, not hallucinations
- **Diff generation** — ask for code changes and get downloadable `git apply`-compatible patches
- **Multi-provider LLM** — Anthropic, OpenAI, Ollama, or any OpenAI-compatible endpoint
- **Local embeddings** — sentence-transformers runs on your machine, no data leaves your network
- **Rate limit awareness** — auto-pause and resume when GitHub or LLM limits are hit
- **Checkpoint/resume** — long ingestions survive interruptions and pick up where they left off

## Quick Start (Docker)

```bash
git clone <your-repo-url> && cd GitHub_Chatbot
cp .env.example .env        # Add your API keys
docker compose up --build
```

Open **http://localhost:8000**. Paste a GitHub repo URL and start chatting.

## Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- Node.js 18+
- Git

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env         # Add your API keys
uvicorn main:app --reload --port 8000
```

The first run downloads the embedding model (~90 MB). Subsequent starts are instant.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The Vite dev server proxies `/api` requests to the backend.

### Production build (without Docker)

```bash
cd frontend && npm run build && cd ..
cd backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

The backend serves the built frontend automatically from `frontend/dist/`.

## Configuration

All settings can be configured via environment variables (`.env`) or the in-app settings UI.

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | *(empty)* | GitHub PAT — required for private repos, raises rate limit to 5K/hr |
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, `ollama`, `custom`, or `claude_code` |
| `LLM_API_KEY` | *(empty)* | API key for cloud providers |
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Model name |
| `LLM_BASE_URL` | *(empty)* | Endpoint URL for Ollama/custom providers |
| `LLM_RATE_LIMIT_TPM` | `0` | Tokens-per-minute limit (0 = disabled) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model for embeddings |

### Using Claude Code (no API key needed — uses your $20/month subscription)

```bash
# .env
LLM_PROVIDER=claude_code
LLM_MODEL=sonnet
```

Requires the Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`) and authenticated (`claude auth`).

### Using Ollama (fully local, no API keys)

```bash
# .env
LLM_PROVIDER=ollama
LLM_MODEL=llama3
LLM_BASE_URL=http://localhost:11434
LLM_RATE_LIMIT_TPM=0
```

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Tailwind CSS, Vite |
| Backend | Python 3.11+, FastAPI, async SQLAlchemy |
| Vector DB | ChromaDB (local, file-based) |
| Database | SQLite (zero config) |
| Git ops | GitPython |
| GitHub API | PyGithub |
| LLM | LiteLLM (multi-provider) |
| Embeddings | sentence-transformers (local) |

Interactive API docs at **http://localhost:8000/docs**.

## License

MIT
