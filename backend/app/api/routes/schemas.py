"""
Pydantic request/response schemas for all API routes.

Using Pydantic models (not dataclasses) so FastAPI can auto-generate
OpenAPI docs and perform validation.
"""

from datetime import datetime

from pydantic import BaseModel


# ── Repositories ──────────────────────────────────────────────────────────────

class RepoIngestRequest(BaseModel):
    url: str  # GitHub repo URL (public or private)


class RepoOut(BaseModel):
    id: str
    name: str          # "owner/repo"
    status: str        # pending | ingesting | ready | failed | syncing | paused
    total_commits: int = 0
    total_files: int = 0
    default_branch: str | None = None
    last_synced_at: datetime | None = None
    metadata: dict | None = None

    model_config = {"from_attributes": True}


class RepoListItem(BaseModel):
    id: str
    name: str
    status: str
    total_commits: int = 0
    last_synced_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Conversations ─────────────────────────────────────────────────────────────

class ConversationOut(BaseModel):
    id: str
    repository_id: str
    title: str
    created_at: datetime
    message_count: int = 0
    llm_provider: str | None = None   # V1.2 — None means "using global setting"
    llm_model: str | None = None      # V1.2 — None means "using global setting"
    has_summary: bool = False         # V1.2 — True when earlier messages are compressed

    model_config = {"from_attributes": True}


class ConversationUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str          # "user" | "assistant"
    content: str
    has_diff: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsOut(BaseModel):
    llm_provider: str
    llm_model: str
    llm_base_url: str
    github_token_set: bool  # Never return the actual token
    embedding_model: str
    llm_rate_limit_tpm: int
    claude_code_available: bool  # True if `claude` CLI binary is in PATH


class ClaudeCodeStatusOut(BaseModel):
    available: bool
    version: str | None
    authenticated: bool
    rate_limit: dict | None
    error: str | None = None


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    github_token: str | None = None
    llm_rate_limit_tpm: int | None = None


class LLMTestRequest(BaseModel):
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""


class GitHubTestRequest(BaseModel):
    token: str = ""  # Empty means "use stored token"


class LLMTestResponse(BaseModel):
    ok: bool
    message: str


# ── Rate Limits ───────────────────────────────────────────────────────────────

class RateLimitServiceOut(BaseModel):
    limit_max: int
    limit_remaining: int
    usage_percent: float
    resets_at: datetime | None
    is_paused: bool


class CheckpointOut(BaseModel):
    operation: str
    stage: str
    progress_current: int
    progress_total: int
    paused_reason: str | None
    resets_at: datetime | None
    paused_at: datetime  # checkpoint.created_at


class ResumeOut(BaseModel):
    id: str
    status: str
    resumed_from: str


# ── Generic ───────────────────────────────────────────────────────────────────

class OkResponse(BaseModel):
    ok: bool = True


class ErrorResponse(BaseModel):
    error: bool = True
    message: str
    detail: str | None = None
