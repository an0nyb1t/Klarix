"""
Shared rate limit manager.

All modules that make external API calls (GitHub, LLM) use this before
each batch of requests. At 95% usage it raises RateLimitExceeded so the
caller can save a checkpoint and stop cleanly.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import RateLimitStatus as RateLimitStatusModel


THRESHOLD = 0.95  # Hard stop at 95% usage


class RateLimitExceeded(Exception):
    """Raised when a service hits the 95% usage threshold."""

    def __init__(self, service: str, usage_percent: float, resets_at: datetime | None):
        self.service = service
        self.usage_percent = usage_percent
        self.resets_at = resets_at
        reset_str = resets_at.strftime("%H:%M UTC") if resets_at else "unknown"
        super().__init__(
            f"{service.upper()} rate limit at {usage_percent:.0%}. "
            f"Hard stop triggered. Resets at {reset_str}."
        )


@dataclass
class RateLimitInfo:
    service: str
    limit_max: int
    limit_remaining: int
    limit_used: int
    usage_percent: float
    resets_at: datetime | None
    is_paused: bool


# In-memory cache so should_pause() is synchronous (no DB call needed)
_cache: dict[str, RateLimitInfo] = {}


def clear_cache() -> None:
    """Clear the in-memory cache. Used in tests to prevent state leaking between cases."""
    _cache.clear()


class RateLimitManager:
    THRESHOLD = THRESHOLD

    def __init__(self, db: AsyncSession):
        self._db = db

    # ── GitHub ──────────────────────────────────────────────────────────────

    async def check_github(self, github_client: Any) -> RateLimitInfo:
        """
        Query GitHub's rate_limit endpoint, persist to DB, and raise if >= 95%.
        Call this before each batch of GitHub API calls.
        """
        rate = github_client.get_rate_limit().rate
        limit_max = rate.limit
        limit_remaining = rate.remaining
        limit_used = limit_max - limit_remaining
        usage_percent = limit_used / limit_max if limit_max > 0 else 0.0
        resets_at = rate.reset  # datetime object from PyGithub

        info = RateLimitInfo(
            service="github",
            limit_max=limit_max,
            limit_remaining=limit_remaining,
            limit_used=limit_used,
            usage_percent=usage_percent,
            resets_at=resets_at,
            is_paused=usage_percent >= THRESHOLD,
        )

        await self._persist(info)
        _cache["github"] = info

        if usage_percent >= THRESHOLD:
            raise RateLimitExceeded("github", usage_percent, resets_at)

        return info

    # ── LLM ─────────────────────────────────────────────────────────────────

    async def track_llm_usage(
        self,
        tokens_used: int,
        provider: str,
        limit_tpm: int,
        resets_at: datetime | None = None,
    ) -> None:
        """
        Called after every LLM completion to accumulate token usage.
        Updates DB + cache. Raises RateLimitExceeded if >= 95% of limit_tpm.
        If limit_tpm is 0, tracking is disabled (local models).
        """
        if limit_tpm == 0:
            return

        existing = _cache.get("llm")
        used_so_far = existing.limit_used if existing else 0
        new_used = used_so_far + tokens_used
        usage_percent = new_used / limit_tpm if limit_tpm > 0 else 0.0

        info = RateLimitInfo(
            service="llm",
            limit_max=limit_tpm,
            limit_remaining=max(0, limit_tpm - new_used),
            limit_used=new_used,
            usage_percent=usage_percent,
            resets_at=resets_at,
            is_paused=usage_percent >= THRESHOLD,
        )

        await self._persist(info)
        _cache["llm"] = info

        if usage_percent >= THRESHOLD:
            raise RateLimitExceeded("llm", usage_percent, resets_at)

    async def reset_llm_usage(self) -> None:
        """Call at the start of each minute window to reset the TPM counter."""
        if "llm" in _cache:
            info = _cache["llm"]
            info.limit_used = 0
            info.limit_remaining = info.limit_max
            info.usage_percent = 0.0
            info.is_paused = False
            _cache["llm"] = info
            await self._persist(info)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def should_pause(self, service: str) -> bool:
        """Synchronous quick-check using in-memory cache. No DB call."""
        info = _cache.get(service)
        return info.is_paused if info else False

    def get_reset_time(self, service: str) -> datetime | None:
        info = _cache.get(service)
        return info.resets_at if info else None

    async def get_status(self, service: str) -> RateLimitInfo | None:
        """Load from DB (used on startup to restore state after restart)."""
        result = await self._db.execute(
            select(RateLimitStatusModel).where(RateLimitStatusModel.service == service)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        info = RateLimitInfo(
            service=row.service,
            limit_max=row.limit_max,
            limit_remaining=row.limit_remaining,
            limit_used=row.limit_used,
            usage_percent=row.usage_percent,
            resets_at=row.resets_at,
            is_paused=row.is_paused,
        )
        _cache[service] = info
        return info

    async def get_all_statuses(self) -> dict[str, RateLimitInfo]:
        result = await self._db.execute(select(RateLimitStatusModel))
        rows = result.scalars().all()
        statuses = {}
        for row in rows:
            info = RateLimitInfo(
                service=row.service,
                limit_max=row.limit_max,
                limit_remaining=row.limit_remaining,
                limit_used=row.limit_used,
                usage_percent=row.usage_percent,
                resets_at=row.resets_at,
                is_paused=row.is_paused,
            )
            statuses[row.service] = info
            _cache[row.service] = info
        return statuses

    async def _persist(self, info: RateLimitInfo) -> None:
        """Upsert rate limit status into the DB."""
        result = await self._db.execute(
            select(RateLimitStatusModel).where(
                RateLimitStatusModel.service == info.service
            )
        )
        row = result.scalar_one_or_none()

        if row is None:
            row = RateLimitStatusModel(service=info.service)
            self._db.add(row)

        row.limit_max = info.limit_max
        row.limit_remaining = info.limit_remaining
        row.limit_used = info.limit_used
        row.usage_percent = info.usage_percent
        row.resets_at = info.resets_at
        row.is_paused = info.is_paused
        row.last_checked_at = datetime.now(timezone.utc)

        await self._db.flush()
