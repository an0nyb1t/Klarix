"""
Stage 4 tests — LLM Provider Layer.

Run with:
    pytest tests/test_stage4.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.llm.config import LLMConfig, default_config, KNOWN_MODELS
from app.llm.exceptions import LLMError
from app.llm.service import LLMService


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_config(**kwargs) -> LLMConfig:
    defaults = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "api_key": "test-key",
        "base_url": "",
        "temperature": 0.3,
        "max_tokens": 4096,
        "rate_limit_tpm": 0,
    }
    defaults.update(kwargs)
    return LLMConfig(**defaults)


def _make_streaming_chunk(content: str, usage=None):
    """Build a mock LiteLLM streaming chunk."""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = MagicMock()
    chunk.choices[0].delta.content = content
    chunk.usage = usage
    return chunk


def _make_sync_response(content: str, total_tokens: int = 100):
    """Build a mock LiteLLM non-streaming response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.total_tokens = total_tokens
    return resp


async def _async_iter(items):
    """Yield items as async iterator."""
    for item in items:
        yield item


# ── LLMConfig ─────────────────────────────────────────────────────────────────

class TestLLMConfig:
    def test_explicit_values_are_preserved(self):
        cfg = LLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key="my-key",
            base_url="",
            rate_limit_tpm=100000,
        )
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o"
        assert cfg.api_key == "my-key"
        assert cfg.rate_limit_tpm == 100000

    def test_defaults_fill_from_settings(self):
        """Empty LLMConfig should fill provider/model from settings."""
        cfg = LLMConfig()
        # Settings defaults: provider="anthropic", model="claude-sonnet-4-20250514"
        assert cfg.provider != ""
        assert cfg.model != ""

    def test_temperature_and_max_tokens_defaults(self):
        cfg = _make_config()
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 4096

    def test_known_models_contains_anthropic(self):
        assert "anthropic" in KNOWN_MODELS
        assert len(KNOWN_MODELS["anthropic"]) > 0

    def test_known_models_contains_openai(self):
        assert "openai" in KNOWN_MODELS
        assert len(KNOWN_MODELS["openai"]) > 0


# ── build_model_string ────────────────────────────────────────────────────────

class TestBuildModelString:
    def setup_method(self):
        self.svc = LLMService()

    def test_anthropic_prefix(self):
        cfg = _make_config(provider="anthropic", model="claude-sonnet-4-6")
        assert self.svc.build_model_string(cfg) == "anthropic/claude-sonnet-4-6"

    def test_openai_prefix(self):
        cfg = _make_config(provider="openai", model="gpt-4o")
        assert self.svc.build_model_string(cfg) == "openai/gpt-4o"

    def test_ollama_prefix(self):
        cfg = _make_config(provider="ollama", model="llama3")
        assert self.svc.build_model_string(cfg) == "ollama/llama3"

    def test_custom_uses_openai_prefix(self):
        cfg = _make_config(provider="custom", model="mistral")
        assert self.svc.build_model_string(cfg) == "openai/mistral"

    def test_unknown_provider_uses_openai_prefix(self):
        cfg = _make_config(provider="groq", model="llama3-8b")
        assert self.svc.build_model_string(cfg) == "openai/llama3-8b"

    def test_provider_is_case_insensitive(self):
        cfg = _make_config(provider="Anthropic", model="claude-haiku-4-5-20251001")
        assert self.svc.build_model_string(cfg) == "anthropic/claude-haiku-4-5-20251001"


# ── _build_kwargs ─────────────────────────────────────────────────────────────

class TestBuildKwargs:
    def setup_method(self):
        self.svc = LLMService()

    def test_includes_model_and_messages(self):
        cfg = _make_config(provider="anthropic", model="claude-sonnet-4-6", api_key="key")
        msgs = [{"role": "user", "content": "Hi"}]
        kwargs = self.svc._build_kwargs(cfg, msgs, stream=True)
        assert kwargs["model"] == "anthropic/claude-sonnet-4-6"
        assert kwargs["messages"] == msgs
        assert kwargs["stream"] is True

    def test_api_key_included_when_present(self):
        cfg = _make_config(api_key="sk-test")
        kwargs = self.svc._build_kwargs(cfg, [], stream=False)
        assert kwargs["api_key"] == "sk-test"

    def test_api_key_omitted_when_empty(self):
        cfg = _make_config(api_key="")
        kwargs = self.svc._build_kwargs(cfg, [], stream=False)
        assert "api_key" not in kwargs

    def test_base_url_included_when_present(self):
        cfg = _make_config(base_url="http://localhost:11434")
        kwargs = self.svc._build_kwargs(cfg, [], stream=False)
        assert kwargs["api_base"] == "http://localhost:11434"

    def test_base_url_omitted_when_empty(self):
        cfg = _make_config(base_url="")
        kwargs = self.svc._build_kwargs(cfg, [], stream=False)
        assert "api_base" not in kwargs

    def test_temperature_and_max_tokens_passed(self):
        cfg = _make_config(temperature=0.7, max_tokens=2048)
        kwargs = self.svc._build_kwargs(cfg, [], stream=False)
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == 2048


# ── Streaming completion ──────────────────────────────────────────────────────

class TestChatCompletion:
    @pytest.mark.asyncio
    async def test_streams_content_chunks(self):
        svc = LLMService()
        cfg = _make_config()
        chunks = [
            _make_streaming_chunk("Hello"),
            _make_streaming_chunk(" world"),
            _make_streaming_chunk("!"),
        ]

        with patch("litellm.acompletion", return_value=_async_iter(chunks)):
            result = []
            async for text in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                result.append(text)

        assert result == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    async def test_skips_empty_chunks(self):
        svc = LLMService()
        cfg = _make_config()
        chunks = [
            _make_streaming_chunk(""),
            _make_streaming_chunk(None),
            _make_streaming_chunk("Real content"),
        ]

        with patch("litellm.acompletion", return_value=_async_iter(chunks)):
            result = []
            async for text in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                result.append(text)

        assert result == ["Real content"]

    @pytest.mark.asyncio
    async def test_auth_error_raises_llm_error(self):
        import litellm as ll
        svc = LLMService()
        cfg = _make_config()

        with patch("litellm.acompletion", side_effect=ll.AuthenticationError("bad key", "anthropic", "400")):
            with pytest.raises(LLMError) as exc_info:
                async for _ in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                    pass
            assert "API key" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_not_found_error_raises_llm_error(self):
        import litellm as ll
        svc = LLMService()
        cfg = _make_config(model="nonexistent-model")

        with patch("litellm.acompletion", side_effect=ll.NotFoundError("not found", "anthropic", "404")):
            with pytest.raises(LLMError) as exc_info:
                async for _ in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                    pass
            assert "not available" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_context_window_error_raises_llm_error(self):
        import litellm as ll
        svc = LLMService()
        cfg = _make_config()

        with patch("litellm.acompletion", side_effect=ll.ContextWindowExceededError("too long", "anthropic", "400")):
            with pytest.raises(LLMError) as exc_info:
                async for _ in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                    pass
            assert "too long" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_connection_error_raises_llm_error(self):
        svc = LLMService()
        cfg = _make_config(provider="ollama", base_url="http://localhost:11434")

        with patch("litellm.acompletion", side_effect=ConnectionError("refused")):
            with pytest.raises(LLMError) as exc_info:
                async for _ in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                    pass
            assert "connect" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_unknown_error_raises_llm_error(self):
        svc = LLMService()
        cfg = _make_config()

        with patch("litellm.acompletion", side_effect=RuntimeError("unexpected")):
            with pytest.raises(LLMError):
                async for _ in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                    pass


# ── Sync completion ───────────────────────────────────────────────────────────

class TestChatCompletionSync:
    @pytest.mark.asyncio
    async def test_returns_full_response(self):
        svc = LLMService()
        cfg = _make_config()
        mock_resp = _make_sync_response("Full answer here", total_tokens=50)

        with patch("litellm.acompletion", return_value=mock_resp):
            result = await svc.chat_completion_sync([{"role": "user", "content": "Hi"}], cfg)

        assert result == "Full answer here"

    @pytest.mark.asyncio
    async def test_returns_empty_string_for_none_content(self):
        svc = LLMService()
        cfg = _make_config()
        mock_resp = _make_sync_response(None)
        mock_resp.choices[0].message.content = None

        with patch("litellm.acompletion", return_value=mock_resp):
            result = await svc.chat_completion_sync([{"role": "user", "content": "Hi"}], cfg)

        assert result == ""

    @pytest.mark.asyncio
    async def test_auth_error_raises_llm_error(self):
        import litellm as ll
        svc = LLMService()
        cfg = _make_config()

        with patch("litellm.acompletion", side_effect=ll.AuthenticationError("bad key", "anthropic", "400")):
            with pytest.raises(LLMError) as exc_info:
                await svc.chat_completion_sync([{"role": "user", "content": "Hi"}], cfg)
            assert "API key" in str(exc_info.value)


# ── Rate limit checks ─────────────────────────────────────────────────────────

class TestRateLimitIntegration:
    @pytest.mark.asyncio
    async def test_raises_rate_limit_exceeded_when_paused(self):
        from rate_limiter import RateLimitExceeded

        mock_rl = MagicMock()
        mock_rl.should_pause.return_value = True

        svc = LLMService(rate_limiter=mock_rl)
        cfg = _make_config(rate_limit_tpm=100000)

        with pytest.raises(RateLimitExceeded):
            async for _ in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                pass

    @pytest.mark.asyncio
    async def test_no_rate_limit_check_when_tpm_zero(self):
        mock_rl = MagicMock()
        mock_rl.should_pause.return_value = True  # Would pause if checked

        svc = LLMService(rate_limiter=mock_rl)
        cfg = _make_config(rate_limit_tpm=0)  # tpm=0 → skip check

        chunks = [_make_streaming_chunk("ok")]
        with patch("litellm.acompletion", return_value=_async_iter(chunks)):
            result = []
            async for text in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                result.append(text)

        # should_pause should never have been called
        mock_rl.should_pause.assert_not_called()
        assert result == ["ok"]

    @pytest.mark.asyncio
    async def test_token_usage_tracked_after_sync_call(self):
        mock_rl = MagicMock()
        mock_rl.should_pause.return_value = False
        mock_rl.track_llm_usage = AsyncMock()

        svc = LLMService(rate_limiter=mock_rl)
        cfg = _make_config(rate_limit_tpm=100000)
        mock_resp = _make_sync_response("answer", total_tokens=200)

        with patch("litellm.acompletion", return_value=mock_resp):
            await svc.chat_completion_sync([{"role": "user", "content": "Hi"}], cfg)

        mock_rl.track_llm_usage.assert_called_once_with(
            tokens_used=200,
            provider="anthropic",
            limit_tpm=100000,
        )

    @pytest.mark.asyncio
    async def test_token_usage_not_tracked_when_tpm_zero(self):
        mock_rl = MagicMock()
        mock_rl.should_pause.return_value = False
        mock_rl.track_llm_usage = AsyncMock()

        svc = LLMService(rate_limiter=mock_rl)
        cfg = _make_config(rate_limit_tpm=0)
        mock_resp = _make_sync_response("answer", total_tokens=200)

        with patch("litellm.acompletion", return_value=mock_resp):
            await svc.chat_completion_sync([{"role": "user", "content": "Hi"}], cfg)

        mock_rl.track_llm_usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_token_usage_tracked_after_streaming(self):
        mock_rl = MagicMock()
        mock_rl.should_pause.return_value = False
        mock_rl.track_llm_usage = AsyncMock()

        svc = LLMService(rate_limiter=mock_rl)
        cfg = _make_config(rate_limit_tpm=50000)

        usage_mock = MagicMock()
        usage_mock.total_tokens = 150
        chunks = [
            _make_streaming_chunk("hello"),
            _make_streaming_chunk("", usage=usage_mock),
        ]

        with patch("litellm.acompletion", return_value=_async_iter(chunks)):
            async for _ in svc.chat_completion([{"role": "user", "content": "Hi"}], cfg):
                pass

        mock_rl.track_llm_usage.assert_called_once_with(
            tokens_used=150,
            provider="anthropic",
            limit_tpm=50000,
        )


# ── list_available_models ─────────────────────────────────────────────────────

class TestListAvailableModels:
    @pytest.mark.asyncio
    async def test_anthropic_returns_known_list(self):
        svc = LLMService()
        cfg = _make_config(provider="anthropic")
        models = await svc.list_available_models(cfg)
        assert len(models) > 0
        assert all(isinstance(m, str) for m in models)

    @pytest.mark.asyncio
    async def test_openai_returns_known_list(self):
        svc = LLMService()
        cfg = _make_config(provider="openai")
        models = await svc.list_available_models(cfg)
        assert "gpt-4o" in models

    @pytest.mark.asyncio
    async def test_ollama_queries_endpoint(self):
        svc = LLMService()
        cfg = _make_config(provider="ollama", base_url="http://localhost:11434")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "llama3"}, {"name": "mistral"}]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            models = await svc.list_available_models(cfg)

        assert "llama3" in models
        assert "mistral" in models

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_empty(self):
        svc = LLMService()
        cfg = _make_config(provider="unknown_provider")

        # Mock httpx to fail so we don't make real network calls
        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(side_effect=ConnectionError("no server"))
            mock_cls.return_value = mock_instance
            models = await svc.list_available_models(cfg)

        assert models == []


# ── test_connection ───────────────────────────────────────────────────────────

class TestConnectionTest:
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        svc = LLMService()
        cfg = _make_config()
        mock_resp = _make_sync_response("ok")

        with patch("litellm.acompletion", return_value=mock_resp):
            result = await svc.test_connection(cfg)

        assert result is True

    @pytest.mark.asyncio
    async def test_raises_llm_error_on_failure(self):
        import litellm as ll
        svc = LLMService()
        cfg = _make_config()

        with patch("litellm.acompletion", side_effect=ll.AuthenticationError("bad", "anthropic", "401")):
            with pytest.raises(LLMError):
                await svc.test_connection(cfg)
