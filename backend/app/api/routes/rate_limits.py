"""
Rate limit and resume routes.

Routes:
  GET  /rate-limits                   Get rate limit status for all services
  POST /repos/{repo_id}/resume        Resume a paused ingestion
  GET  /repos/{repo_id}/checkpoint    Get checkpoint details for a paused repo
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.schemas import CheckpointOut, RateLimitServiceOut, ResumeOut
from checkpoint import CheckpointManager
from database import get_db
from models import Repository
from rate_limiter import RateLimitManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Rate Limits"])


@router.get("/rate-limits", response_model=dict[str, RateLimitServiceOut])
async def get_rate_limits(db: AsyncSession = Depends(get_db)):
    """
    Get current rate limit status for all tracked services (github, llm).
    Loads from the in-memory cache first, then DB as fallback.
    """
    rate_mgr = RateLimitManager(db)
    statuses = await rate_mgr.get_all_statuses()

    return {
        service: RateLimitServiceOut(
            limit_max=info.limit_max,
            limit_remaining=info.limit_remaining,
            usage_percent=info.usage_percent,
            resets_at=info.resets_at,
            is_paused=info.is_paused,
        )
        for service, info in statuses.items()
    }


@router.post("/repos/{repo_id}/resume", response_model=ResumeOut, status_code=202)
async def resume_repo(
    repo_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Resume a paused ingestion or sync operation.
    Loads the checkpoint and continues from where it left off.
    Returns 409 if the repo is not paused or has no checkpoint.
    """
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_id}' not found.")

    if repo.status != "paused":
        raise HTTPException(
            status_code=409,
            detail=f"Repository is not paused (status: {repo.status}).",
        )

    cp_mgr = CheckpointManager(db)
    # Try ingestion checkpoint first, then resync
    checkpoint = await cp_mgr.load(repo_id, "ingestion")
    if checkpoint is None:
        checkpoint = await cp_mgr.load(repo_id, "resync")

    if checkpoint is None:
        raise HTTPException(
            status_code=409,
            detail="No active checkpoint found. Cannot resume.",
        )

    resumed_from = checkpoint.stage

    # Mark repo as actively ingesting so GET /repos/{id} shows current state
    repo.status = "ingesting"
    await db.flush()
    await db.commit()

    # Schedule resume as background task
    background_tasks.add_task(_run_resume, repo_id, repo.url, checkpoint.operation)

    return ResumeOut(
        id=repo_id,
        status="ingesting",
        resumed_from=resumed_from,
    )


@router.get("/repos/{repo_id}/checkpoint", response_model=CheckpointOut)
async def get_checkpoint(repo_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get checkpoint details for a paused repository.
    Returns 404 if no active checkpoint exists.
    """
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_id}' not found.")

    cp_mgr = CheckpointManager(db)
    checkpoint = await cp_mgr.load(repo_id, "ingestion")
    if checkpoint is None:
        checkpoint = await cp_mgr.load(repo_id, "resync")

    if checkpoint is None:
        raise HTTPException(
            status_code=404,
            detail="No active checkpoint for this repository.",
        )

    return CheckpointOut(
        operation=checkpoint.operation,
        stage=checkpoint.stage,
        progress_current=checkpoint.progress_current,
        progress_total=checkpoint.progress_total,
        paused_reason=checkpoint.paused_reason,
        resets_at=checkpoint.resets_at,
        paused_at=checkpoint.created_at,
    )


# ── Background task ───────────────────────────────────────────────────────────

async def _run_resume(repo_id: str, repo_url: str, operation: str) -> None:
    """Background task: resume an ingestion or resync from its checkpoint."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            if operation == "ingestion":
                from app.ingester.service import ingest_repository
                await ingest_repository(repo_url, db, existing_repo_id=repo_id)
            else:
                from app.ingester.service import resync_repository
                await resync_repository(repo_id, db)
        except Exception as e:
            logger.error("Background resume failed for %s: %s", repo_id, e)
