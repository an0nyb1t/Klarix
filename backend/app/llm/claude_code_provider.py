"""
Claude Code CLI provider — wraps the `claude` CLI as a text-in/text-out LLM.

SECURITY CONTRACT (enforced by this module):
- ALL tools are disabled via --tools "" on every invocation
- subprocess is spawned with create_subprocess_exec (never shell=True)
- User input is passed as list arguments (never interpolated into strings)
- The -- separator prevents flag injection from user prompts
- Subprocess environment is sanitized — no backend secrets leak to the CLI
- Only parsed text content from assistant events is ever yielded
- The subprocess is killed (not cancelled) on timeout
- stderr is logged but never sent to the caller

Any deviation from these rules is a security bug.
"""

import asyncio
import json
import logging
import os
import shutil
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from app.llm.exceptions import LLMError

logger = logging.getLogger(__name__)

# CLI subprocess timeout in seconds
_CLI_TIMEOUT = 120

# Allowed environment variable keys passed to the subprocess.
# This whitelist prevents backend secrets (API keys, tokens, DB URLs)
# from leaking into the Claude Code CLI process.
_ALLOWED_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
})

# Module-level rate limit cache — updated from CLI rate_limit_event responses
_rate_limit_cache: dict | None = None


# ── Environment helpers ───────────────────────────────────────────────────────

def _clean_env() -> dict[str, str]:
    """
    Return a sanitized copy of the environment for the claude subprocess.

    Only passes variables required for the CLI to find its binary dependencies
    and config directory. Explicitly excludes all backend secrets.
    """
    return {k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_KEYS}


def _get_claude_binary() -> str:
    """
    Resolve the absolute path of the claude binary via shutil.which().

    Using the absolute path (not a bare "claude" string) prevents PATH
    manipulation attacks and makes it clear exactly what binary is executed.

    Raises LLMError if the binary is not found.
    """
    path = shutil.which("claude")
    if path is None:
        raise LLMError(
            "Claude Code CLI not found in PATH. "
            "Install it with: npm install -g @anthropic-ai/claude-code"
        )
    return path


# ── Rate limit cache ──────────────────────────────────────────────────────────

def _update_rate_limit_cache(event: dict) -> None:
    """Update the module-level cache from a rate_limit_event payload."""
    global _rate_limit_cache
    info = event.get("rate_limit_info", {})
    resets_at = None
    if info.get("resetsAt"):
        try:
            resets_at = datetime.fromtimestamp(info["resetsAt"], tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass

    _rate_limit_cache = {
        "status": info.get("status"),
        "resets_at": resets_at,
        "rate_limit_type": info.get("rateLimitType"),
        "last_checked_at": datetime.now(timezone.utc),
    }


def get_cached_rate_limit() -> dict | None:
    """Return the most recent rate limit info received from the CLI."""
    return _rate_limit_cache


# ── Message format conversion ─────────────────────────────────────────────────

def _build_cli_args(messages: list[dict]) -> tuple[str, str]:
    """
    Convert an LLM messages array into CLI arguments.

    Returns: (system_prompt, user_prompt)

    Strategy:
    - First message with role="system" → --system-prompt flag
    - Intermediate history (user/assistant pairs) → appended to system prompt
      as a "Conversation so far:" block so the model has context
    - Last message with role="user" → the CLI prompt argument (after --)
    """
    system_parts: list[str] = []
    history_parts: list[str] = []
    user_prompt = ""

    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            system_parts.append(content)
        elif i == len(messages) - 1 and role == "user":
            # Last message is the current user turn
            user_prompt = content
        elif role in ("user", "assistant"):
            # Intermediate history
            label = "User" if role == "user" else "Assistant"
            history_parts.append(f"{label}: {content}")

    system_prompt = "\n\n".join(system_parts) if system_parts else ""

    if history_parts:
        system_prompt = (
            system_prompt
            + "\n\n--- Conversation so far ---\n"
            + "\n\n".join(history_parts)
            + "\n--- End conversation ---"
        )

    return system_prompt, user_prompt


def _build_command(binary: str, model: str, system_prompt: str, user_prompt: str) -> list[str]:
    """
    Build the CLI argument list.

    SECURITY: All arguments are list elements — never interpolated into strings.
    The -- separator before the prompt prevents any user input from being
    interpreted as CLI flags.
    """
    args = [
        binary,
        "-p",                          # print mode — non-interactive
        "--output-format", "stream-json",
        "--verbose",                   # required for stream-json
        "--model", model,
        "--tools", "",                 # SECURITY: disable ALL tools
        "--no-session-persistence",    # don't write to ~/.claude/
    ]

    if system_prompt:
        args += ["--system-prompt", system_prompt]

    # -- separator prevents user_prompt from being parsed as flags
    args += ["--", user_prompt]

    return args


# ── Subprocess streaming ──────────────────────────────────────────────────────

async def _stream_cli(command: list[str]) -> AsyncGenerator[str, None]:
    """
    Spawn the claude CLI and stream parsed text chunks from stdout.

    SECURITY guarantees enforced here:
    - create_subprocess_exec (not shell) — no shell interpretation
    - env=_clean_env() — no backend secrets passed to subprocess
    - Only text content from "assistant" events is yielded
    - stderr is logged, never yielded
    - Process is killed on timeout

    Raises LLMError on CLI errors or timeout.
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_clean_env(),              # SECURITY: sanitized environment
    )

    got_result = False
    error_message: str | None = None

    try:
        async with asyncio.timeout(_CLI_TIMEOUT):
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # Parse JSON — skip malformed lines with a warning
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Claude Code CLI: skipping malformed JSON line: %.100s", line)
                    continue

                event_type = event.get("type")

                if event_type == "assistant":
                    # SECURITY: only yield parsed text content, never raw lines
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                yield text

                elif event_type == "rate_limit_event":
                    _update_rate_limit_cache(event)

                elif event_type == "result":
                    got_result = True
                    if event.get("is_error"):
                        error_message = event.get("result", "Unknown CLI error")
                    # Non-error result — streaming is complete

                elif event_type in ("system",):
                    # Informational — log at debug level, don't yield
                    logger.debug("Claude Code CLI init: session_id=%s", event.get("session_id"))

    except asyncio.TimeoutError:
        # SECURITY: kill (not just terminate) to ensure process ends
        process.kill()
        await process.wait()
        raise LLMError(
            f"Claude Code CLI timed out after {_CLI_TIMEOUT} seconds. "
            "The request may have been too complex or the CLI is unresponsive."
        )

    # Collect any stderr output
    try:
        stderr_data = await asyncio.wait_for(process.stderr.read(), timeout=5.0)
        if stderr_data:
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            if stderr_text:
                # SECURITY: log stderr but never yield to caller
                logger.warning("Claude Code CLI stderr: %s", stderr_text[:500])
    except asyncio.TimeoutError:
        pass

    await process.wait()

    if error_message:
        # Check for rate limit errors from the CLI
        lower = error_message.lower()
        if "rate limit" in lower or "usage limit" in lower or "quota" in lower:
            raise LLMError(
                f"Claude Code subscription limit reached. {error_message}"
            )
        raise LLMError(f"Claude Code CLI error: {error_message}")

    if not got_result and process.returncode != 0:
        raise LLMError(
            f"Claude Code CLI exited with code {process.returncode}. "
            "Check that the CLI is authenticated: run 'claude auth'."
        )


# ── Public API ────────────────────────────────────────────────────────────────

class ClaudeCodeProvider:
    """Wraps the Claude Code CLI as a secure text-in/text-out LLM provider."""

    @staticmethod
    async def check_available() -> tuple[bool, str]:
        """
        Check if the claude CLI is installed and authenticated.

        Returns (True, version_string) on success.
        Returns (False, error_message) on failure.

        Does NOT raise — always returns a tuple.
        """
        # Step 1: binary in PATH?
        binary = shutil.which("claude")
        if binary is None:
            return False, (
                "Claude Code CLI not found in PATH. "
                "Install it with: npm install -g @anthropic-ai/claude-code"
            )

        # Step 2: get version (fast, no network)
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_clean_env(),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            version = stdout.decode("utf-8", errors="replace").strip()
        except Exception as e:
            return False, f"Claude Code CLI found but failed to run: {e}"

        # Step 3: verify authentication with a minimal prompt
        try:
            auth_command = [
                binary,
                "-p",
                "--output-format", "json",
                "--tools", "",              # SECURITY: no tools
                "--no-session-persistence",
                "--",
                "Reply with just the word: ok",
            ]
            proc = await asyncio.create_subprocess_exec(
                *auth_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_clean_env(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            output = stdout.decode("utf-8", errors="replace").strip()

            # Try to parse as JSON result
            try:
                result = json.loads(output)
                if result.get("is_error"):
                    err = result.get("result", "Unknown error")
                    if "auth" in err.lower() or "login" in err.lower() or "token" in err.lower():
                        return False, (
                            "Claude Code CLI is not authenticated. "
                            "Run 'claude auth' to log in."
                        )
                    return False, f"Claude Code CLI error: {err}"
            except json.JSONDecodeError:
                # If output isn't JSON, check for error indicators
                if proc.returncode != 0:
                    stderr_text = stderr.decode("utf-8", errors="replace").strip()
                    if "auth" in stderr_text.lower() or "login" in stderr_text.lower():
                        return False, (
                            "Claude Code CLI is not authenticated. "
                            "Run 'claude auth' to log in."
                        )
                    return False, f"Claude Code CLI failed (exit {proc.returncode})"

        except asyncio.TimeoutError:
            return False, "Claude Code CLI authentication check timed out."
        except Exception as e:
            return False, f"Claude Code CLI check failed: {e}"

        return True, version

    @staticmethod
    async def stream(
        messages: list[dict],
        config: "LLMConfig",  # noqa: F821 — avoid circular import
    ) -> AsyncGenerator[str, None]:
        """
        Stream response text chunks from the Claude Code CLI.

        The caller receives exactly what the LLM writes — no system info,
        no file paths, no error traces, no raw CLI output.

        Raises LLMError on CLI errors, timeouts, or rate limit exhaustion.
        """
        binary = _get_claude_binary()
        model = config.model or "sonnet"

        system_prompt, user_prompt = _build_cli_args(messages)

        if not user_prompt:
            raise LLMError("No user message found in the message list.")

        command = _build_command(binary, model, system_prompt, user_prompt)

        logger.debug(
            "Claude Code CLI: model=%s, system_len=%d, prompt_len=%d",
            model, len(system_prompt), len(user_prompt),
        )

        async for chunk in _stream_cli(command):
            yield chunk

    @staticmethod
    def get_rate_limit_status() -> dict | None:
        """Return the most recent cached rate limit info from the CLI."""
        return get_cached_rate_limit()
