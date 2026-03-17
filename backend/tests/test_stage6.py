"""
Stage 6 tests — API Layer (FastAPI routes).

Run with:
    pytest tests/test_stage6.py -v

Uses httpx AsyncClient with the FastAPI app mounted on an in-memory DB.
WebSocket tests use the built-in TestClient.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import database


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(test_db_engine):
    """
    AsyncClient pointed at the FastAPI app.
    Uses the patched in-memory DB from conftest.
    """
    # Import app after DB is patched
    from main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Repositories ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_repo_returns_202(client):
    """POST /api/repos should return 202 and start background ingestion."""
    with patch("app.api.routes.repositories._run_ingestion"):
        resp = await client.post(
            "/api/repos",
            json={"url": "https://github.com/owner/testrepo"},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["name"] == "owner/testrepo"
    assert data["status"] == "ingesting"
    assert "id" in data


@pytest.mark.asyncio
async def test_ingest_repo_invalid_url(client):
    """POST /api/repos with a non-GitHub URL returns 400."""
    resp = await client.post(
        "/api/repos",
        json={"url": "https://notgithub.com/foo/bar"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingest_repo_duplicate_returns_existing(client, db):
    """POST /api/repos for an already-ingested URL returns the existing record (not a new one)."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/existing",
        name="owner/existing",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    with patch("app.api.routes.repositories._run_ingestion"):
        resp = await client.post(
            "/api/repos",
            json={"url": "https://github.com/owner/existing"},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["id"] == repo.id
    assert data["name"] == "owner/existing"


@pytest.mark.asyncio
async def test_list_repos_empty(client):
    """GET /api/repos returns empty list when no repos exist."""
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_repo_not_found(client):
    """GET /api/repos/{id} returns 404 for unknown repo."""
    resp = await client.get("/api/repos/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_repo_success(client, db):
    """GET /api/repos/{id} returns repo details."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/myrepo",
        name="owner/myrepo",
        status="ready",
        total_commits=100,
        total_files=50,
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    resp = await client.get(f"/api/repos/{repo.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == repo.id
    assert data["name"] == "owner/myrepo"
    assert data["status"] == "ready"
    assert data["total_commits"] == 100


@pytest.mark.asyncio
async def test_delete_repo(client, db):
    """DELETE /api/repos/{id} removes the repo."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/deleteme",
        name="owner/deleteme",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    with patch("app.knowledge_base.store._get_client", side_effect=Exception("no chroma")):
        resp = await client.delete(f"/api/repos/{repo.id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify deleted
    resp2 = await client.get(f"/api/repos/{repo.id}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_sync_repo_not_ready(client, db):
    """POST /api/repos/{id}/sync returns 409 if repo isn't ready."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/syncme",
        name="owner/syncme",
        status="ingesting",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    resp = await client.post(f"/api/repos/{repo.id}/sync")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_sync_repo_success(client, db):
    """POST /api/repos/{id}/sync on a ready repo returns 202 and sets status to syncing."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/syncready",
        name="owner/syncready",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    with patch("app.api.routes.repositories._run_resync"):
        resp = await client.post(f"/api/repos/{repo.id}/sync")
    assert resp.status_code == 202
    data = resp.json()
    assert data["id"] == repo.id
    assert data["status"] == "syncing"


# ── Conversations ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_conversation(client, db):
    """POST /api/repos/{id}/conversations creates a conversation."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/chatrepo",
        name="owner/chatrepo",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    resp = await client.post(f"/api/repos/{repo.id}/conversations")
    assert resp.status_code == 201
    data = resp.json()
    assert data["repository_id"] == repo.id
    assert data["title"] == "New conversation"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_conversation_repo_not_ready(client, db):
    """POST conversation returns 409 if repo is still ingesting."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/notready",
        name="owner/notready",
        status="ingesting",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    resp = await client.post(f"/api/repos/{repo.id}/conversations")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_conversations(client, db):
    """GET /api/repos/{id}/conversations returns conversations."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/listconv",
        name="owner/listconv",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    # Create two conversations
    await client.post(f"/api/repos/{repo.id}/conversations")
    await client.post(f"/api/repos/{repo.id}/conversations")

    resp = await client.get(f"/api/repos/{repo.id}/conversations")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_messages_empty(client, db):
    """GET /conversations/{id}/messages returns empty list for new conversation."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/msgtest",
        name="owner/msgtest",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    conv_resp = await client.post(f"/api/repos/{repo.id}/conversations")
    conv_id = conv_resp.json()["id"]

    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_delete_conversation(client, db):
    """DELETE /conversations/{id} removes the conversation."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/delconv",
        name="owner/delconv",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    conv_resp = await client.post(f"/api/repos/{repo.id}/conversations")
    conv_id = conv_resp.json()["id"]

    resp = await client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200

    # Verify deleted
    resp2 = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp2.status_code == 404


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_settings(client):
    """GET /api/settings returns settings without exposing tokens."""
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_provider" in data
    assert "llm_model" in data
    assert "github_token_set" in data
    assert isinstance(data["github_token_set"], bool)
    # V1.1: claude_code_available must be present and boolean
    assert "claude_code_available" in data
    assert isinstance(data["claude_code_available"], bool)
    # Must NOT return actual token
    assert "github_token" not in data
    assert "llm_api_key" not in data


@pytest.mark.asyncio
async def test_update_settings(client):
    """PUT /api/settings updates persisted settings."""
    resp = await client.put(
        "/api/settings",
        json={"llm_provider": "openai", "llm_model": "gpt-4o"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify the update was applied
    get_resp = await client.get("/api/settings")
    data = get_resp.json()
    assert data["llm_provider"] == "openai"
    assert data["llm_model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_test_llm_success(client):
    """POST /api/settings/test-llm returns ok=True on success."""
    with patch.object(
        __import__("app.llm.service", fromlist=["LLMService"]).LLMService,
        "test_connection",
        new_callable=AsyncMock,
        return_value=True,
    ):
        resp = await client.post(
            "/api/settings/test-llm",
            json={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "anthropic" in data["message"]


@pytest.mark.asyncio
async def test_test_llm_failure(client):
    """POST /api/settings/test-llm returns ok=False on LLMError."""
    from app.llm.exceptions import LLMError

    with patch.object(
        __import__("app.llm.service", fromlist=["LLMService"]).LLMService,
        "test_connection",
        side_effect=LLMError("Cannot connect to provider"),
    ):
        resp = await client.post(
            "/api/settings/test-llm",
            json={"provider": "ollama", "model": "llama3", "base_url": "http://localhost:11434"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "Cannot connect" in data["message"]


@pytest.mark.asyncio
async def test_test_llm_claude_code_success(client):
    """POST /api/settings/test-llm for claude_code returns tailored success message."""
    with patch.object(
        __import__("app.llm.service", fromlist=["LLMService"]).LLMService,
        "test_connection",
        new_callable=AsyncMock,
        return_value=True,
    ):
        resp = await client.post(
            "/api/settings/test-llm",
            json={"provider": "claude_code", "model": "sonnet"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "Claude Code" in data["message"]
    assert "authenticated" in data["message"].lower()


@pytest.mark.asyncio
async def test_claude_code_status_available(client):
    """GET /api/claude-code/status returns available=True when CLI is installed."""
    from app.llm.claude_code_provider import ClaudeCodeProvider

    with patch.object(
        ClaudeCodeProvider,
        "check_available",
        new=AsyncMock(return_value=(True, "1.2.3")),
    ), patch(
        "app.llm.claude_code_provider.get_cached_rate_limit",
        return_value={
            "status": "allowed",
            "resets_at": None,
            "rate_limit_type": "five_hour",
            "last_checked_at": None,
        },
    ):
        resp = await client.get("/api/claude-code/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["version"] == "1.2.3"
    assert data["authenticated"] is True
    assert data["rate_limit"] is not None
    assert data["rate_limit"]["status"] == "allowed"
    assert "error" not in data or data["error"] is None


@pytest.mark.asyncio
async def test_claude_code_status_not_available(client):
    """GET /api/claude-code/status returns available=False when CLI is missing."""
    from app.llm.claude_code_provider import ClaudeCodeProvider

    with patch.object(
        ClaudeCodeProvider,
        "check_available",
        new=AsyncMock(return_value=(False, "Claude Code CLI not found in PATH.")),
    ):
        resp = await client.get("/api/claude-code/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["version"] is None
    assert data["authenticated"] is False
    assert data["rate_limit"] is None
    assert "not found" in data["error"].lower()


@pytest.mark.asyncio
async def test_claude_code_status_no_rate_limit_cache(client):
    """GET /api/claude-code/status returns rate_limit=null before any streaming call."""
    from app.llm.claude_code_provider import ClaudeCodeProvider

    with patch.object(
        ClaudeCodeProvider,
        "check_available",
        new=AsyncMock(return_value=(True, "1.2.3")),
    ), patch(
        "app.llm.claude_code_provider.get_cached_rate_limit",
        return_value=None,
    ):
        resp = await client.get("/api/claude-code/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["rate_limit"] is None


# ── Rate Limits ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_rate_limits_empty(client):
    """GET /api/rate-limits returns empty dict when no data recorded yet."""
    resp = await client.get("/api/rate-limits")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


@pytest.mark.asyncio
async def test_get_checkpoint_not_found(client, db):
    """GET /api/repos/{id}/checkpoint returns 404 when no checkpoint."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/nocheckpoint",
        name="owner/nocheckpoint",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    resp = await client.get(f"/api/repos/{repo.id}/checkpoint")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resume_not_paused(client, db):
    """POST /api/repos/{id}/resume returns 409 if repo isn't paused."""
    from models import Repository
    repo = Repository(
        url="https://github.com/owner/notpaused",
        name="owner/notpaused",
        status="ready",
    )
    db.add(repo)
    await db.flush()
    await db.commit()

    resp = await client.post(f"/api/repos/{repo.id}/resume")
    assert resp.status_code == 409
