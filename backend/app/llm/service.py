"""
LLM provider service — routes to LiteLLM or the Claude Code CLI provider.

For all providers except "claude_code", delegates to LiteLLM (Anthropic, OpenAI,
Ollama, custom OpenAI-compatible endpoints).

For "claude_code", delegates to ClaudeCodeProvider which wraps the `claude` CLI.

Tracks token usage and enforces a hard stop at 95% of the configured TPM limit.
"""

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import litellm

from app.llm.claude_code_provider import ClaudeCodeProvider
from app.llm.config import KNOWN_MODELS, LLMConfig, default_config
from app.llm.exceptions import LLMError
from rate_limiter import RateLimitExceeded, RateLimitManager

logger = logging.getLogger(__name__)

# Suppress LiteLLM's verbose logging unless debug mode
litellm.suppress_debug_info = True


class LLMService:
    def __init__(self, rate_limiter: RateLimitManager | None = None):
        self._rate_limiter = rate_limiter

    # ── Public interface ──────────────────────────────────────────────────────

    async def chat_completion(
        self,
        messages: list[dict],
        config: LLMConfig | None = None,
        stream: bool = True,
    ) -> AsyncGenerator[str, None]:
        """
        Send messages to the LLM and yield response text chunks (streaming).

        Routes to ClaudeCodeProvider for provider="claude_code", otherwise LiteLLM.

        Yields:
            str — each content chunk as it arrives from the model.

        Raises:
            LLMError — user-friendly message for any provider error.
            RateLimitExceeded — if TPM limit is at 95%.
        """
        cfg = config or default_config()
        self._check_rate_limit(cfg)

        # Route to Claude Code CLI provider
        if cfg.provider == "claude_code":
            async for chunk in ClaudeCodeProvider.stream(messages, cfg):
                yield chunk
            return

        model_string = self.build_model_string(cfg)
        kwargs = self._build_kwargs(cfg, messages, stream=True)

        logger.debug("LLM call: model=%s, messages=%d", model_string, len(messages))

        try:
            response = await litellm.acompletion(**kwargs)
            total_tokens = 0

            async for chunk in response:
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None) or ""
                if content:
                    yield content

                # Extract usage from final chunk (LiteLLM populates usage on last chunk)
                if hasattr(chunk, "usage") and chunk.usage:
                    total_tokens = getattr(chunk.usage, "total_tokens", 0) or 0

        except litellm.AuthenticationError as e:
            raise LLMError(f"Invalid API key for {cfg.provider}. Check your settings.") from e
        except litellm.NotFoundError as e:
            raise LLMError(
                f"Model '{cfg.model}' not available on {cfg.provider}. "
                "Check the model name or switch providers."
            ) from e
        except litellm.ContextWindowExceededError as e:
            raise LLMError(
                "Input too long for this model. Try a shorter query or switch to a model "
                "with a larger context window."
            ) from e
        except litellm.RateLimitError as e:
            raise LLMError(
                f"Rate limit hit on {cfg.provider}. Wait a moment and try again."
            ) from e
        except (ConnectionError, litellm.APIConnectionError) as e:
            base = cfg.base_url or "the provider endpoint"
            raise LLMError(
                f"Could not connect to {base}. Is the LLM server running?"
            ) from e
        except Exception as e:
            raise LLMError(f"LLM error: {e}") from e

        # Track usage after streaming completes
        if total_tokens > 0:
            await self._track_usage(cfg, total_tokens)

    async def chat_completion_sync(
        self,
        messages: list[dict],
        config: LLMConfig | None = None,
    ) -> str:
        """
        Non-streaming completion. Returns the full response as a string.
        Useful for internal calls (test_connection, system tasks).

        Routes to ClaudeCodeProvider for provider="claude_code", otherwise LiteLLM.
        """
        cfg = config or default_config()
        self._check_rate_limit(cfg)

        # Route to Claude Code CLI provider — collect all chunks into one string
        if cfg.provider == "claude_code":
            parts: list[str] = []
            async for chunk in ClaudeCodeProvider.stream(messages, cfg):
                parts.append(chunk)
            return "".join(parts)

        kwargs = self._build_kwargs(cfg, messages, stream=False)

        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.AuthenticationError as e:
            raise LLMError(f"Invalid API key for {cfg.provider}. Check your settings.") from e
        except litellm.NotFoundError as e:
            raise LLMError(
                f"Model '{cfg.model}' not available on {cfg.provider}. "
                "Check the model name or switch providers."
            ) from e
        except litellm.ContextWindowExceededError as e:
            raise LLMError(
                "Input too long for this model. Try a shorter query or switch to a model "
                "with a larger context window."
            ) from e
        except litellm.RateLimitError as e:
            raise LLMError(
                f"Rate limit hit on {cfg.provider}. Wait a moment and try again."
            ) from e
        except (ConnectionError, litellm.APIConnectionError) as e:
            base = cfg.base_url or "the provider endpoint"
            raise LLMError(
                f"Could not connect to {base}. Is the LLM server running?"
            ) from e
        except Exception as e:
            raise LLMError(f"LLM error: {e}") from e

        content = response.choices[0].message.content or ""

        if hasattr(response, "usage") and response.usage:
            total_tokens = getattr(response.usage, "total_tokens", 0) or 0
            if total_tokens > 0:
                await self._track_usage(cfg, total_tokens)

        return content

    def build_model_string(self, config: LLMConfig) -> str:
        """
        Convert provider + model to LiteLLM's expected model string format.

        Examples:
          anthropic + claude-sonnet-4-20250514 → "anthropic/claude-sonnet-4-20250514"
          openai + gpt-4o → "openai/gpt-4o"
          ollama + llama3 → "ollama/llama3"
          custom + mistral → "openai/mistral"  (custom uses openai-compat)
        """
        provider = config.provider.lower()
        model = config.model

        if provider in ("anthropic", "openai", "ollama"):
            return f"{provider}/{model}"
        else:
            # "custom" and unknown providers — treat as OpenAI-compatible
            return f"openai/{model}"

    async def test_connection(self, config: LLMConfig) -> bool:
        """
        Send a tiny test message to verify the provider config works.

        For claude_code: uses ClaudeCodeProvider.check_available() which checks
        binary presence AND authentication without consuming subscription quota.

        Returns True on success. Raises LLMError with a descriptive message on failure.
        """
        if config.provider == "claude_code":
            available, message = await ClaudeCodeProvider.check_available()
            if not available:
                raise LLMError(message)
            return True

        test_messages = [{"role": "user", "content": "Reply with just: ok"}]
        # Use low max_tokens to keep the test cheap
        test_config = LLMConfig(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            temperature=0.0,
            max_tokens=10,
            rate_limit_tpm=0,  # Skip rate limit tracking for test
        )
        await self.chat_completion_sync(test_messages, test_config)
        return True

    async def list_available_models(self, config: LLMConfig) -> list[str]:
        """
        Return available models for the configured provider.

        For cloud providers (Anthropic, OpenAI, claude_code): return curated hardcoded list.
        For Ollama / custom: query the /api/tags or /v1/models endpoint.
        """
        provider = config.provider.lower()

        if provider in KNOWN_MODELS:
            return KNOWN_MODELS[provider]

        if provider in ("ollama", "custom"):
            return await self._fetch_ollama_models(config)

        return []

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_kwargs(
        self,
        config: LLMConfig,
        messages: list[dict],
        stream: bool,
    ) -> dict:
        """Build the kwargs dict for litellm.acompletion()."""
        model_string = self.build_model_string(config)
        kwargs: dict = {
            "model": model_string,
            "messages": messages,
            "stream": stream,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }

        if config.api_key:
            kwargs["api_key"] = config.api_key

        if config.base_url:
            kwargs["api_base"] = config.base_url

        return kwargs

    def _check_rate_limit(self, config: LLMConfig) -> None:
        """Raise RateLimitExceeded if the LLM TPM is at 95%. Skip if tpm=0."""
        if config.rate_limit_tpm == 0:
            return
        if self._rate_limiter and self._rate_limiter.should_pause("llm"):
            resets_at = self._rate_limiter.get_reset_time("llm")
            from rate_limiter import _cache
            cached = _cache.get("llm")
            usage = cached.usage_percent if cached else 0.95
            raise RateLimitExceeded(
                service="llm",
                usage_percent=usage,
                resets_at=resets_at,
            )

    async def _track_usage(self, config: LLMConfig, total_tokens: int) -> None:
        """Report token usage to the rate limiter."""
        if config.rate_limit_tpm == 0 or not self._rate_limiter:
            return
        try:
            await self._rate_limiter.track_llm_usage(
                tokens_used=total_tokens,
                provider=config.provider,
                limit_tpm=config.rate_limit_tpm,
            )
        except RateLimitExceeded:
            raise
        except Exception as e:
            logger.warning("Failed to track LLM usage: %s", e)

    async def _fetch_ollama_models(self, config: LLMConfig) -> list[str]:
        """Query Ollama or OpenAI-compatible /v1/models endpoint for available models."""
        import httpx

        base = config.base_url.rstrip("/") if config.base_url else "http://localhost:11434"

        # Try OpenAI-compatible /v1/models first
        endpoints = [f"{base}/v1/models", f"{base}/api/tags"]

        for url in endpoints:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()

                    # OpenAI format: {"data": [{"id": "model-name"}, ...]}
                    if "data" in data:
                        return [m["id"] for m in data["data"] if "id" in m]

                    # Ollama format: {"models": [{"name": "llama3"}, ...]}
                    if "models" in data:
                        return [m["name"] for m in data["models"] if "name" in m]

            except Exception:
                continue

        return []
