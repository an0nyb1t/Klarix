"""
LLM provider configuration.

LLMConfig can be constructed from .env defaults or overridden per-request.
"""

from dataclasses import dataclass

from config import settings


@dataclass
class LLMConfig:
    provider: str = ""            # "anthropic", "openai", "ollama", "custom", "claude_code"
    model: str = ""               # e.g. "claude-sonnet-4-20250514", "gpt-4o", "llama3", "sonnet"
    api_key: str = ""             # Empty for local models
    base_url: str = ""            # Only for Ollama / custom endpoints
    temperature: float = 0.3
    max_tokens: int = 4096
    rate_limit_tpm: int = 0       # 0 = no tracking (local models)

    def __post_init__(self):
        # Fill in from settings if not explicitly provided
        if not self.provider:
            self.provider = settings.llm_provider
        if not self.model:
            self.model = settings.llm_model
        if not self.api_key:
            self.api_key = settings.llm_api_key
        if not self.base_url:
            self.base_url = settings.llm_base_url
        if not self.rate_limit_tpm:
            self.rate_limit_tpm = settings.llm_rate_limit_tpm


def default_config() -> LLMConfig:
    """Return LLMConfig loaded from .env settings."""
    return LLMConfig()


# Hardcoded popular models per cloud provider — returned by list_available_models()
KNOWN_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
    # Claude Code CLI provider — short model aliases accepted by `claude -p --model`
    "claude_code": [
        "sonnet",
        "opus",
        "haiku",
    ],
}
