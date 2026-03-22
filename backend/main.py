"""
GitChat — FastAPI application entry point.

Run with:
    uvicorn main:app --reload --port 8000
or:
    python main.py
"""

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.llm.exceptions import LLMError
from config import settings
from database import init_db, migrate_v12, migrate_v13

logger = logging.getLogger(__name__)


async def _reset_tpm_loop() -> None:
    """Background task: reset the LLM TPM counter once per minute."""
    from database import AsyncSessionLocal
    from rate_limiter import RateLimitManager

    while True:
        await asyncio.sleep(60)
        try:
            async with AsyncSessionLocal() as db:
                mgr = RateLimitManager(db)
                await mgr.reset_llm_usage()
                await db.commit()
        except Exception:
            logger.exception("Failed to reset LLM TPM counter")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    # Ensure the data directory exists (repos, ChromaDB, SQLite all live here)
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(os.path.join(settings.data_dir, "repos"), exist_ok=True)
    os.makedirs(os.path.join(settings.data_dir, "chromadb"), exist_ok=True)

    # Create all database tables
    await init_db()

    # Run V1.2 migrations (adds columns if not present — safe to re-run)
    await migrate_v12()

    # Run V1.3 migrations (adds patch_ready column to repositories)
    await migrate_v13()

    # Load user-saved settings from DB into the in-memory singleton
    from app.api.routes.settings import _apply_settings_to_memory
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await _apply_settings_to_memory(db)

    # V1.3 backfill: set patch_ready=True for repos that already have working clones
    from app.ingester.service import _backfill_working_clones
    await _backfill_working_clones()

    # Start background task to reset LLM TPM counter every minute
    tpm_reset_task = asyncio.create_task(_reset_tpm_loop())

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    tpm_reset_task.cancel()
    try:
        await tpm_reset_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="GitChat API",
    description="Chat with any GitHub repository.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — wide open for local dev (self-hosted tool, no auth)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"error": True, "message": str(exc)},
    )


@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    return JSONResponse(
        status_code=500,
        content={"error": True, "message": str(exc)},
    )


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    detail = str(exc) if settings.debug else None
    return JSONResponse(
        status_code=500,
        content={"error": True, "message": "An internal error occurred.", "detail": detail},
    )


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}


# ── Routers ───────────────────────────────────────────────────────────────────

from app.api.routes import repositories, conversations, chat, settings as settings_routes, rate_limits

app.include_router(repositories.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(settings_routes.router, prefix="/api")
app.include_router(rate_limits.router, prefix="/api")


# ── Serve built frontend in production ───────────────────────────────────────
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    def _shutdown(sig, frame):
        print("\nShutting down GitChat…")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
