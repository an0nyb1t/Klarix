"""
Settings routes.

Routes:
  GET  /settings              Get current settings
  PUT  /settings              Update settings
  POST /settings/test-llm     Test LLM connection
  POST /settings/test-github  Test GitHub token validity
  GET  /claude-code/status    Claude Code CLI availability + rate limit info
"""

import asyncio
import logging
import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.schemas import ClaudeCodeStatusOut, GitHubTestRequest, LLMTestRequest, LLMTestResponse, OkResponse, SettingsOut, SettingsUpdateRequest
from app.llm.config import LLMConfig
from app.llm.exceptions import LLMError
from app.llm.service import LLMService
from config import settings
from database import get_db
from models import Setting

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Settings"])


async def _get_all_settings(db: AsyncSession) -> dict[str, str]:
    """Load all settings from DB as a key→value dict."""
    result = await db.execute(select(Setting))
    return {row.key: row.value for row in result.scalars().all()}


async def _apply_settings_to_memory(db: AsyncSession) -> None:
    """Apply DB-persisted settings to the in-memory settings singleton."""
    db_settings = await _get_all_settings(db)

    if "llm_provider" in db_settings:
        settings.llm_provider = db_settings["llm_provider"]
    if "llm_model" in db_settings:
        settings.llm_model = db_settings["llm_model"]
    if "llm_base_url" in db_settings:
        settings.llm_base_url = db_settings["llm_base_url"]
    if "llm_api_key" in db_settings:
        settings.llm_api_key = db_settings["llm_api_key"]
    if "github_token" in db_settings:
        settings.github_token = db_settings["github_token"]
    if "llm_rate_limit_tpm" in db_settings:
        settings.llm_rate_limit_tpm = int(db_settings["llm_rate_limit_tpm"])


async def _upsert_setting(db: AsyncSession, key: str, value: str) -> None:
    """Insert or update a single setting."""
    result = await db.execute(select(Setting).where(Setting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    await db.flush()


@router.get("/settings", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    """
    Get current settings.
    DB values take priority over env defaults.
    API keys and tokens are never returned — only a boolean indicating whether they're set.
    """
    db_settings = await _get_all_settings(db)

    return SettingsOut(
        llm_provider=db_settings.get("llm_provider", settings.llm_provider),
        llm_model=db_settings.get("llm_model", settings.llm_model),
        llm_base_url=db_settings.get("llm_base_url", settings.llm_base_url),
        github_token_set=bool(
            db_settings.get("github_token") or settings.github_token
        ),
        embedding_model=settings.embedding_model,
        llm_rate_limit_tpm=int(
            db_settings.get("llm_rate_limit_tpm", settings.llm_rate_limit_tpm)
        ),
        claude_code_available=shutil.which("claude") is not None,
    )


@router.put("/settings", response_model=OkResponse)
async def update_settings(
    body: SettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Update settings. Only provided fields are updated.
    Changes take effect immediately for new LLM calls and ingestion tasks.
    """
    updates: dict[str, str] = {}

    if body.llm_provider is not None:
        updates["llm_provider"] = body.llm_provider
        settings.llm_provider = body.llm_provider

    if body.llm_model is not None:
        updates["llm_model"] = body.llm_model
        settings.llm_model = body.llm_model

    if body.llm_base_url is not None:
        updates["llm_base_url"] = body.llm_base_url
        settings.llm_base_url = body.llm_base_url

    if body.llm_api_key is not None:
        updates["llm_api_key"] = body.llm_api_key
        settings.llm_api_key = body.llm_api_key

    if body.github_token is not None:
        updates["github_token"] = body.github_token
        settings.github_token = body.github_token

    if body.llm_rate_limit_tpm is not None:
        updates["llm_rate_limit_tpm"] = str(body.llm_rate_limit_tpm)
        settings.llm_rate_limit_tpm = body.llm_rate_limit_tpm

    for key, value in updates.items():
        await _upsert_setting(db, key, value)

    await db.commit()
    return OkResponse()


@router.post("/settings/test-llm", response_model=LLMTestResponse)
async def test_llm(body: LLMTestRequest):
    """
    Test an LLM connection with the given provider/model config.
    Sends a minimal completion request to verify connectivity.

    For claude_code: verifies CLI is installed and authenticated (no quota consumed).
    For all others: sends a minimal prompt via LiteLLM.
    """
    config = LLMConfig(
        provider=body.provider,
        model=body.model,
        api_key=body.api_key,
        base_url=body.base_url,
        temperature=0.0,
        max_tokens=10,
        rate_limit_tpm=0,
    )

    llm_svc = LLMService()
    try:
        await llm_svc.test_connection(config)
        if body.provider == "claude_code":
            return LLMTestResponse(ok=True, message="Claude Code CLI is installed and authenticated.")
        return LLMTestResponse(
            ok=True,
            message=f"Connected successfully to {body.provider}/{body.model}",
        )
    except LLMError as e:
        return LLMTestResponse(ok=False, message=str(e))
    except Exception as e:
        return LLMTestResponse(ok=False, message=f"Unexpected error: {e}")


@router.post("/settings/test-github", response_model=LLMTestResponse)
async def test_github(body: GitHubTestRequest, db: AsyncSession = Depends(get_db)):
    """
    Test a GitHub token by making a lightweight /user API call.
    If no token is provided in the body, falls back to the stored token.
    Returns the authenticated username on success.
    """
    from github import Github, GithubException

    token = body.token
    if not token:
        db_settings = await _get_all_settings(db)
        token = db_settings.get("github_token") or settings.github_token

    if not token:
        return LLMTestResponse(ok=False, message="No GitHub token provided or stored.")

    def _check():
        g = Github(token)
        return g.get_user().login

    try:
        login = await asyncio.to_thread(_check)
        return LLMTestResponse(ok=True, message=f"Connected as {login}")
    except GithubException as e:
        return LLMTestResponse(ok=False, message=f"GitHub error {e.status}: {e.data.get('message', str(e))}")
    except Exception as e:
        return LLMTestResponse(ok=False, message=str(e))


@router.get("/claude-code/status", response_model=ClaudeCodeStatusOut)
async def claude_code_status():
    """
    Check Claude Code CLI availability and current rate limit status.

    Returns version and authenticated=True if the CLI is installed and logged in.
    Returns available=False with an error message if not.
    Rate limit info is cached from the last streaming call — null if no call has been made yet.
    """
    from app.llm.claude_code_provider import ClaudeCodeProvider, get_cached_rate_limit

    available, message = await ClaudeCodeProvider.check_available()

    if not available:
        return ClaudeCodeStatusOut(
            available=False,
            version=None,
            authenticated=False,
            rate_limit=None,
            error=message,
        )

    rate_limit = get_cached_rate_limit()
    return ClaudeCodeStatusOut(
        available=True,
        version=message,  # check_available() returns the version string on success
        authenticated=True,
        rate_limit=rate_limit,
    )
