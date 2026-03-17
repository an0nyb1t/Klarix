# ─────────────────────────────────────────────────────────────────────────────
# GitChat — Multi-stage Docker build
#
# Stage 1: Build the React frontend
# Stage 2: Run the FastAPI backend (serves built frontend as static files)
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Frontend build ──────────────────────────────────────────────────
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Backend + serve ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# System deps for git operations and building native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Copy built frontend into the location main.py expects
# main.py resolves: os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
# With __file__=/app/main.py → /app/../frontend/dist → /frontend/dist
COPY --from=frontend-build /app/frontend/dist /frontend/dist

# Create data directory
RUN mkdir -p /app/data/repos /app/data/chromadb

# Pre-download the default embedding model so first run is fast
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
