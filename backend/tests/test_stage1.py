"""
Stage 1 tests — backend foundation.

Run with:
    pytest tests/test_stage1.py -v
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text

from main import app
from config import settings
from rate_limiter import RateLimitManager, RateLimitExceeded
from checkpoint import CheckpointManager


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ── Health endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── Config ───────────────────────────────────────────────────────────────────

def test_config_defaults():
    assert settings.app_name == "GitChat"
    assert settings.data_dir == "./data"
    assert "sqlite" in settings.database_url


# ── Database tables ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_tables_created(db):
    expected_tables = [
        "repositories",
        "conversations",
        "messages",
        "settings",
        "checkpoints",
        "rate_limit_status",
    ]
    for table in expected_tables:
        result = await db.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        )
        assert result.scalar() == table, f"Table '{table}' was not created"


# ── CheckpointManager ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkpoint_save_and_load(db):
    mgr = CheckpointManager(db)
    repo_id = "test-repo-123"

    cp = await mgr.save(
        repo_id=repo_id,
        operation="ingestion",
        stage="fetching_issues",
        progress_current=45,
        progress_total=87,
        state={"completed_issues": [1, 2, 3], "clone_complete": True},
        paused_reason="github_rate_limit",
    )
    await db.commit()

    assert cp.id is not None
    assert cp.stage == "fetching_issues"
    assert cp.progress_current == 45

    loaded = await mgr.load(repo_id, "ingestion")
    assert loaded is not None
    assert loaded.id == cp.id
    assert loaded.state["completed_issues"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_checkpoint_mark_resumed(db):
    mgr = CheckpointManager(db)
    repo_id = "test-repo-456"

    cp = await mgr.save(
        repo_id=repo_id,
        operation="ingestion",
        stage="cloning",
        progress_current=0,
        progress_total=0,
        state={},
    )
    await db.commit()

    await mgr.mark_resumed(cp.id)
    await db.commit()

    # Resumed checkpoints should not be loaded
    loaded = await mgr.load(repo_id, "ingestion")
    assert loaded is None


@pytest.mark.asyncio
async def test_checkpoint_clear(db):
    mgr = CheckpointManager(db)
    repo_id = "test-repo-789"

    await mgr.save(
        repo_id=repo_id,
        operation="knowledge_base_build",
        stage="embedding_chunks",
        progress_current=100,
        progress_total=500,
        state={"chunks_stored": 100},
    )
    await db.commit()

    await mgr.clear(repo_id, "knowledge_base_build")
    await db.commit()

    loaded = await mgr.load(repo_id, "knowledge_base_build")
    assert loaded is None


@pytest.mark.asyncio
async def test_checkpoint_update(db):
    """update() should modify stage, progress, and state of an existing checkpoint."""
    mgr = CheckpointManager(db)
    repo_id = "test-repo-update"

    cp = await mgr.save(
        repo_id=repo_id,
        operation="ingestion",
        stage="fetching_issues",
        progress_current=10,
        progress_total=100,
        state={"completed_issues": [1, 2]},
    )
    await db.commit()

    await mgr.update(
        checkpoint_id=cp.id,
        stage="fetching_prs",
        progress_current=50,
        progress_total=100,
        state={"completed_issues": [1, 2, 3, 4, 5], "completed_prs": [10]},
    )
    await db.commit()

    loaded = await mgr.load(repo_id, "ingestion")
    assert loaded is not None
    assert loaded.stage == "fetching_prs"
    assert loaded.progress_current == 50
    assert loaded.state["completed_prs"] == [10]
    assert len(loaded.state["completed_issues"]) == 5


@pytest.mark.asyncio
async def test_checkpoint_returns_none_when_empty(db):
    mgr = CheckpointManager(db)
    result = await mgr.load("nonexistent-repo", "ingestion")
    assert result is None


# ── RateLimitManager ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_rate_limit_no_tracking_when_tpm_zero(db):
    """When limit_tpm=0, no exception is raised regardless of usage."""
    mgr = RateLimitManager(db)
    # Should not raise even with massive token usage
    await mgr.track_llm_usage(tokens_used=999999, provider="anthropic", limit_tpm=0)


@pytest.mark.asyncio
async def test_llm_rate_limit_raises_at_95_percent(db):
    """Should raise RateLimitExceeded when usage reaches >= 95% of TPM."""
    mgr = RateLimitManager(db)
    limit = 1000

    with pytest.raises(RateLimitExceeded) as exc_info:
        # 960 tokens = 96% of 1000
        await mgr.track_llm_usage(tokens_used=960, provider="anthropic", limit_tpm=limit)

    assert exc_info.value.service == "llm"
    assert exc_info.value.usage_percent >= 0.95


@pytest.mark.asyncio
async def test_llm_rate_limit_accumulates_across_calls(db):
    """Token usage accumulates across multiple calls before raising."""
    mgr = RateLimitManager(db)
    limit = 1000

    # First call: 50% — no raise
    await mgr.track_llm_usage(tokens_used=500, provider="anthropic", limit_tpm=limit)

    # Second call: another 45% brings total to 95% — should raise
    with pytest.raises(RateLimitExceeded):
        await mgr.track_llm_usage(tokens_used=450, provider="anthropic", limit_tpm=limit)


@pytest.mark.asyncio
async def test_should_pause_returns_false_initially(db):
    mgr = RateLimitManager(db)
    assert mgr.should_pause("github") is False
    assert mgr.should_pause("llm") is False


@pytest.mark.asyncio
async def test_rate_limit_exception_message(db):
    mgr = RateLimitManager(db)
    with pytest.raises(RateLimitExceeded) as exc_info:
        await mgr.track_llm_usage(tokens_used=1000, provider="anthropic", limit_tpm=1000)
    err = exc_info.value
    assert "LLM" in str(err)
    assert "95%" in str(err) or "100%" in str(err)
