"""
Repository routes — CRUD + ingestion + SSE progress + patch apply.

Routes:
  POST   /repos                          Start ingesting a new repository
  GET    /repos                          List all repositories
  GET    /repos/{repo_id}                Get repository details
  POST   /repos/{repo_id}/sync           Re-sync a repository
  DELETE /repos/{repo_id}                Delete a repository
  GET    /repos/{repo_id}/progress       SSE stream for ingestion/sync progress
  POST   /repos/{repo_id}/apply-patch    Apply a unified diff to the working clone
"""

import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.schemas import (
    OkResponse,
    PatchApplyRequest,
    PatchApplyResponse,
    RepoIngestRequest,
    RepoListItem,
    RepoOut,
)
from app.ingester.service import clear_progress, get_progress, ingest_repository, resync_repository
from database import get_db
from models import Repository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Repositories"])


# ── Background task wrappers ──────────────────────────────────────────────────

async def _run_ingestion(repo_url: str, repo_id: str) -> None:
    """Background task: run full ingestion pipeline with its own DB session."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            await ingest_repository(repo_url, db, existing_repo_id=repo_id)
        except Exception as e:
            logger.error("Background ingestion failed for %s: %s", repo_url, e)


async def _run_resync(repo_id: str) -> None:
    """Background task: run re-sync with its own DB session."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            await resync_repository(repo_id, db)
        except Exception as e:
            logger.error("Background resync failed for %s: %s", repo_id, e)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/repos", response_model=RepoOut, status_code=202)
async def ingest_repo(
    body: RepoIngestRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Start ingesting a new repository.
    Returns immediately; ingestion runs in the background.
    Poll GET /repos/{repo_id}/progress for status.
    """
    from app.ingester.url_parser import InvalidGitHubURL, parse_github_url
    try:
        parsed = parse_github_url(body.url)
    except InvalidGitHubURL as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check if already ingested
    result = await db.execute(
        select(Repository).where(Repository.url == parsed.canonical_url)
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.status in ("ingesting", "syncing"):
            raise HTTPException(
                status_code=409,
                detail=f"Repository is already being {existing.status}.",
            )
        # Re-return existing record if already done
        return _repo_to_out(existing)

    # Create repo record immediately so we can return an ID
    repo = Repository(
        url=parsed.canonical_url,
        name=parsed.full_name,
        status="ingesting",
    )
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    background_tasks.add_task(_run_ingestion, body.url, repo.id)
    return _repo_to_out(repo)


@router.get("/repos", response_model=list[RepoListItem])
async def list_repos(db: AsyncSession = Depends(get_db)):
    """List all repositories, most recently created first."""
    result = await db.execute(
        select(Repository).order_by(Repository.created_at.desc())
    )
    repos = result.scalars().all()
    return [RepoListItem(
        id=r.id,
        name=r.name,
        status=r.status,
        total_commits=r.total_commits,
        last_synced_at=r.last_synced_at,
        patch_ready=r.patch_ready or False,
    ) for r in repos]


@router.get("/repos/{repo_id}", response_model=RepoOut)
async def get_repo(repo_id: str, db: AsyncSession = Depends(get_db)):
    """Get full repository details including metadata and ingestion status."""
    repo = await _get_repo_or_404(repo_id, db)
    return _repo_to_out(repo)


@router.post("/repos/{repo_id}/sync", response_model=RepoOut, status_code=202)
async def sync_repo(
    repo_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Re-sync a repository — fetch new commits, issues, and PRs.
    Returns immediately; sync runs in the background.
    """
    repo = await _get_repo_or_404(repo_id, db)

    if repo.status in ("ingesting", "syncing"):
        raise HTTPException(
            status_code=409,
            detail=f"Repository is already {repo.status}.",
        )
    if repo.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="Repository must be in 'ready' status to sync.",
        )

    repo.status = "syncing"
    await db.flush()
    await db.commit()

    background_tasks.add_task(_run_resync, repo_id)
    return _repo_to_out(repo)


@router.delete("/repos/{repo_id}", response_model=OkResponse)
async def delete_repo(repo_id: str, db: AsyncSession = Depends(get_db)):
    """
    Delete a repository and all associated data:
    - Conversations + messages (via cascade)
    - Knowledge base (ChromaDB collection)
    - Cloned git directory
    """
    repo = await _get_repo_or_404(repo_id, db)

    # Delete ChromaDB collection
    try:
        from app.knowledge_base.store import _get_client, _collection_name
        chroma_client = _get_client()
        try:
            chroma_client.delete_collection(_collection_name(repo_id))
        except Exception:
            pass  # Collection may not exist if ingestion didn't finish
    except Exception as e:
        logger.warning("Failed to delete ChromaDB collection for %s: %s", repo_id, e)

    # Delete cloned git directories (mirror + working clone)
    try:
        from app.ingester.url_parser import parse_github_url
        from config import settings
        import shutil
        import os
        parsed = parse_github_url(repo.url)
        # Mirror clone (bare)
        mirror_path = os.path.join(settings.data_dir, "repos", f"{parsed.owner}_{parsed.repo}.git")
        if os.path.exists(mirror_path):
            await asyncio.to_thread(shutil.rmtree, mirror_path)
        # Working clone (V1.3)
        working_path = os.path.join(settings.project_root, "data", "repos", f"{parsed.owner}_{parsed.repo}")
        if os.path.exists(working_path):
            await asyncio.to_thread(shutil.rmtree, working_path)
    except Exception as e:
        logger.warning("Failed to delete clone directories for %s: %s", repo_id, e)

    # Delete DB record (cascades to conversations, messages, checkpoints)
    await db.delete(repo)
    await db.commit()

    return OkResponse()


@router.get("/repos/{repo_id}/progress")
async def get_repo_progress(repo_id: str, db: AsyncSession = Depends(get_db)):
    """
    SSE endpoint — streams ingestion/sync progress events.

    Events:
      progress  — { stage, current, total, message }
      paused    — { reason, usage_percent, resets_at, message }
      complete  — { repo_id }
      error     — { message }
    """
    # Verify repo exists
    await _get_repo_or_404(repo_id, db)

    async def _event_stream():
        idle_ticks = 0

        while True:
            progress = get_progress(repo_id)

            if progress is None:
                # No progress yet — repo may be queued
                idle_ticks += 1
                if idle_ticks > 60:  # 30s with no progress → stop
                    yield _sse_event("error", {"message": "No progress received. Ingestion may have crashed."})
                    return
                await asyncio.sleep(0.5)
                continue

            idle_ticks = 0

            if progress.stage == "complete":
                yield _sse_event("complete", {"repo_id": repo_id})
                clear_progress(repo_id)
                return

            if progress.stage == "failed":
                yield _sse_event("error", {"message": progress.message})
                clear_progress(repo_id)
                return

            if progress.stage == "paused":
                resets_at_str = progress.resets_at.isoformat() if progress.resets_at else None
                yield _sse_event("paused", {
                    "reason": progress.paused_reason,
                    "usage_percent": None,
                    "resets_at": resets_at_str,
                    "message": progress.message,
                })
                # Keep the SSE connection open — client waits for resume
                await asyncio.sleep(5)
                continue

            # Normal progress event — send on every tick
            yield _sse_event("progress", {
                "stage": progress.stage,
                "current": progress.current,
                "total": progress.total,
                "message": progress.message,
            })

            await asyncio.sleep(0.5)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post("/repos/{repo_id}/apply-patch", response_model=PatchApplyResponse)
async def apply_patch_endpoint(
    repo_id: str,
    body: PatchApplyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Apply a unified diff to the repository's working clone.
    Returns HTTP 409 if the working clone is not ready yet.
    """
    import os
    from app.ingester.git_service import apply_patch, sync_mirror_from_working
    from app.ingester.url_parser import parse_github_url
    from app.ingester.service import _working_clone_path, _clone_path

    repo = await _get_repo_or_404(repo_id, db)

    if not repo.patch_ready:
        raise HTTPException(
            status_code=409,
            detail="Working clone not ready yet. Please wait for background cloning to complete.",
        )

    parsed = parse_github_url(repo.url)
    working_path = _working_clone_path(parsed)
    mirror_path = _clone_path(parsed)

    result = await asyncio.to_thread(apply_patch, working_path, body.patch)

    if result["success"]:
        try:
            await asyncio.to_thread(sync_mirror_from_working, working_path, mirror_path)
        except Exception as e:
            logger.warning("Mirror sync failed after patch apply for %s: %s", repo_id, e)

    return PatchApplyResponse(**result)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_repo_or_404(repo_id: str, db: AsyncSession) -> Repository:
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_id}' not found.")
    return repo


def _repo_to_out(repo: Repository) -> RepoOut:
    return RepoOut(
        id=repo.id,
        name=repo.name,
        status=repo.status,
        total_commits=repo.total_commits,
        total_files=repo.total_files,
        default_branch=repo.default_branch,
        last_synced_at=repo.last_synced_at,
        metadata=repo.metadata_json,
        patch_ready=repo.patch_ready or False,
    )


def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
