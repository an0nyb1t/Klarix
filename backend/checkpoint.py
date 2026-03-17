"""
Checkpoint manager for resumable long-running operations.

Ingestion and knowledge-base builds can take minutes. If they're
interrupted (rate limit, crash, restart), the checkpoint lets the
operation continue exactly where it left off — no duplicate work.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models import Checkpoint as CheckpointModel


@dataclass
class Checkpoint:
    id: str
    repository_id: str
    operation: str        # ingestion | knowledge_base_build | resync
    stage: str
    progress_current: int
    progress_total: int
    state: dict           # Everything needed to resume
    paused_reason: str | None
    resets_at: datetime | None
    created_at: datetime
    resumed_at: datetime | None


class CheckpointManager:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def save(
        self,
        repo_id: str,
        operation: str,
        stage: str,
        progress_current: int,
        progress_total: int,
        state: dict,
        paused_reason: str = "",
        resets_at: datetime | None = None,
    ) -> Checkpoint:
        """
        Save current progress. Creates a new checkpoint row.
        Called when a rate limit hits 95% or as a periodic save during long ops.
        """
        row = CheckpointModel(
            repository_id=repo_id,
            operation=operation,
            stage=stage,
            progress_current=progress_current,
            progress_total=progress_total,
            state_json=state,
            paused_reason=paused_reason or None,
            resets_at=resets_at,
        )
        self._db.add(row)
        await self._db.flush()
        await self._db.refresh(row)
        return self._to_dataclass(row)

    async def load(self, repo_id: str, operation: str) -> Checkpoint | None:
        """
        Load the most recent unresolved (not yet resumed) checkpoint for this
        repo + operation. Returns None if no checkpoint exists.
        """
        result = await self._db.execute(
            select(CheckpointModel)
            .where(
                and_(
                    CheckpointModel.repository_id == repo_id,
                    CheckpointModel.operation == operation,
                    CheckpointModel.resumed_at.is_(None),
                )
            )
            .order_by(CheckpointModel.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return self._to_dataclass(row) if row else None

    async def update(
        self,
        checkpoint_id: str,
        stage: str,
        progress_current: int,
        progress_total: int,
        state: dict,
    ) -> None:
        """Update an existing checkpoint with new progress (for periodic saves)."""
        result = await self._db.execute(
            select(CheckpointModel).where(CheckpointModel.id == checkpoint_id)
        )
        row = result.scalar_one_or_none()
        if row:
            row.stage = stage
            row.progress_current = progress_current
            row.progress_total = progress_total
            row.state_json = state
            await self._db.flush()

    async def mark_resumed(self, checkpoint_id: str) -> None:
        """Mark a checkpoint as resumed so it won't be loaded again."""
        result = await self._db.execute(
            select(CheckpointModel).where(CheckpointModel.id == checkpoint_id)
        )
        row = result.scalar_one_or_none()
        if row:
            row.resumed_at = datetime.now(timezone.utc)
            await self._db.flush()

    async def clear(self, repo_id: str, operation: str) -> None:
        """
        Mark all unresolved checkpoints for this repo+operation as resumed.
        Called when an operation completes successfully.
        """
        result = await self._db.execute(
            select(CheckpointModel).where(
                and_(
                    CheckpointModel.repository_id == repo_id,
                    CheckpointModel.operation == operation,
                    CheckpointModel.resumed_at.is_(None),
                )
            )
        )
        rows = result.scalars().all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.resumed_at = now
        if rows:
            await self._db.flush()

    def _to_dataclass(self, row: CheckpointModel) -> Checkpoint:
        return Checkpoint(
            id=row.id,
            repository_id=row.repository_id,
            operation=row.operation,
            stage=row.stage,
            progress_current=row.progress_current,
            progress_total=row.progress_total,
            state=row.state_json or {},
            paused_reason=row.paused_reason,
            resets_at=row.resets_at,
            created_at=row.created_at,
            resumed_at=row.resumed_at,
        )
