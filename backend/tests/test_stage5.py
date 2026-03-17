"""
Stage 5 tests — Chat Engine (RAG pipeline).

Run with:
    pytest tests/test_stage5.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.chat.prompts import (
    CHARS_PER_TOKEN,
    assemble_messages,
    build_context_block,
    build_system_prompt,
    compute_context_budget,
)
from app.chat.rag import (
    analyze_query,
    contains_diff,
    enhance_question_for_diff,
    is_change_request,
)
from app.knowledge_base.schemas import RetrievedChunk


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_chunk(
    chunk_id: str,
    content: str,
    chunk_type: str = "code",
    file_path: str = "src/main.py",
    score: float = 0.9,
) -> RetrievedChunk:
    meta: dict = {"type": chunk_type, "repo_id": "test-repo"}
    if chunk_type == "code":
        meta.update({"file_path": file_path, "line_start": 1, "line_end": 10})
    elif chunk_type == "commit":
        meta.update({"short_hash": "abc1234", "author": "Alice", "date": "2024-01-15"})
    elif chunk_type == "issue":
        meta.update({"number": 42, "title": "Bug in parser", "state": "open"})
    elif chunk_type == "pull_request":
        meta.update({"number": 7, "title": "Fix login", "state": "merged"})
    elif chunk_type == "repo_overview":
        pass
    return RetrievedChunk(
        chunk_id=chunk_id,
        content=content,
        metadata=meta,
        similarity_score=score,
    )


# ── RAG: Query Analysis ───────────────────────────────────────────────────────

class TestQueryAnalysis:
    def test_code_keywords_detected(self):
        types = analyze_query("What does the parse function do?")
        assert "code" in types

    def test_commit_keywords_detected(self):
        types = analyze_query("Who changed the login module?")
        assert "commit" in types

    def test_issue_keywords_detected(self):
        types = analyze_query("Is there an open bug for the parser?")
        assert "issue" in types

    def test_pr_keywords_detected(self):
        types = analyze_query("Show me the pull request that merged the feature")
        assert "pull_request" in types

    def test_repo_overview_always_included(self):
        # Even for code questions, overview should be in the types
        types = analyze_query("What does the parse function do?")
        assert "repo_overview" in types

    def test_generic_question_returns_none(self):
        # "hello" has no keyword matches → search all types
        types = analyze_query("hello")
        assert types is None

    def test_multiple_types_detected(self):
        types = analyze_query("What commits fixed the login bug?")
        assert "commit" in types
        assert "issue" in types

    def test_case_insensitive(self):
        types = analyze_query("WHAT FUNCTION handles authentication?")
        assert "code" in types


# ── RAG: Diff Detection ───────────────────────────────────────────────────────

class TestDiffDetection:
    def test_detects_diff_block(self):
        response = "Here is the fix:\n```diff\n--- a/main.py\n+++ b/main.py\n@@ -1,3 +1,4 @@\n+import os\n```"
        assert contains_diff(response) is True

    def test_no_diff_block(self):
        response = "The function does X by calling Y."
        assert contains_diff(response) is False

    def test_is_change_request_add(self):
        assert is_change_request("Add error handling to the login function") is True

    def test_is_change_request_fix(self):
        assert is_change_request("Fix the null pointer bug") is True

    def test_is_change_request_refactor(self):
        assert is_change_request("Refactor the database module") is True

    def test_not_change_request(self):
        assert is_change_request("What does the login function do?") is False

    def test_enhance_question_adds_diff_instructions(self):
        enhanced = enhance_question_for_diff("Add a timeout")
        assert "unified diff" in enhanced
        assert "git apply" in enhanced
        assert "Add a timeout" in enhanced


# ── Prompts: System Prompt ────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_contains_repo_name(self):
        prompt = build_system_prompt("owner/myrepo", "A great project", "Python")
        assert "owner/myrepo" in prompt

    def test_contains_description(self):
        prompt = build_system_prompt("owner/repo", "Security scanner", "Go")
        assert "Security scanner" in prompt

    def test_contains_language(self):
        prompt = build_system_prompt("owner/repo", "", "Rust")
        assert "Rust" in prompt

    def test_empty_description_uses_fallback(self):
        prompt = build_system_prompt("owner/repo", "", "Python")
        assert "No description" in prompt

    def test_contains_diff_instructions(self):
        prompt = build_system_prompt("owner/repo", "desc", "Python")
        assert "git apply" in prompt


# ── Prompts: Context Block ────────────────────────────────────────────────────

class TestContextBlock:
    def test_empty_chunks_returns_empty_string(self):
        result = build_context_block([], max_context_tokens=1000)
        assert result == ""

    def test_code_chunk_formatted_with_file_path(self):
        chunk = _make_chunk("c1", "def foo(): pass", chunk_type="code")
        result = build_context_block([chunk], max_context_tokens=1000)
        assert "[Code]" in result
        assert "src/main.py" in result
        assert "def foo(): pass" in result

    def test_commit_chunk_formatted(self):
        chunk = _make_chunk("c2", "Fixed login bug", chunk_type="commit")
        result = build_context_block([chunk], max_context_tokens=1000)
        assert "[Commit]" in result
        assert "abc1234" in result

    def test_issue_chunk_formatted(self):
        chunk = _make_chunk("c3", "Login fails on Safari", chunk_type="issue")
        result = build_context_block([chunk], max_context_tokens=1000)
        assert "[Issue]" in result
        assert "#42" in result

    def test_pr_chunk_formatted(self):
        chunk = _make_chunk("c4", "Merged the fix", chunk_type="pull_request")
        result = build_context_block([chunk], max_context_tokens=1000)
        assert "[PR]" in result
        assert "#7" in result

    def test_chunks_that_exceed_budget_are_skipped(self):
        # Create a chunk that's too large for the budget
        big_content = "x" * 10000
        chunk = _make_chunk("big", big_content, chunk_type="code")
        # Budget of 10 tokens = 40 chars — can't fit the big chunk
        result = build_context_block([chunk], max_context_tokens=10)
        assert result == ""

    def test_includes_context_markers(self):
        chunk = _make_chunk("c1", "some code", chunk_type="code")
        result = build_context_block([chunk], max_context_tokens=1000)
        assert "--- Relevant Context ---" in result
        assert "--- End Context ---" in result

    def test_multiple_chunks_all_included_when_fits(self):
        chunks = [
            _make_chunk("c1", "code1", chunk_type="code"),
            _make_chunk("c2", "code2", chunk_type="code"),
        ]
        result = build_context_block(chunks, max_context_tokens=2000)
        assert "code1" in result
        assert "code2" in result


# ── Prompts: Assemble Messages ────────────────────────────────────────────────


def _mock_msg(role: str, content: str):
    """Create a mock Message object with role and content attributes."""
    m = MagicMock()
    m.role = role
    m.content = content
    return m


class TestAssembleMessages:
    def test_first_message_is_system(self):
        msgs = assemble_messages("System prompt", "Context", None, [], "User question")
        assert msgs[0]["role"] == "system"

    def test_system_includes_context(self):
        msgs = assemble_messages("Sys", "Context block", None, [], "Q")
        assert "Context block" in msgs[0]["content"]

    def test_last_message_is_user_question(self):
        msgs = assemble_messages("Sys", "Ctx", None, [], "What is X?")
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "What is X?"

    def test_history_inserted_between_system_and_user(self):
        history = [
            _mock_msg("user", "prev question"),
            _mock_msg("assistant", "prev answer"),
        ]
        msgs = assemble_messages("Sys", "Ctx", None, history, "New question")
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "prev question"
        assert msgs[2]["role"] == "assistant"
        assert msgs[-1]["content"] == "New question"

    def test_no_context_block_still_has_system(self):
        msgs = assemble_messages("Sys prompt", "", None, [], "Q")
        assert msgs[0]["role"] == "system"
        assert "Sys prompt" in msgs[0]["content"]

    def test_total_message_count(self):
        history = [_mock_msg("user", "a"), _mock_msg("assistant", "b")]
        msgs = assemble_messages("S", "C", None, history, "Q")
        # system + 2 history + user question = 4
        assert len(msgs) == 4

    def test_summary_injected_when_present(self):
        msgs = assemble_messages("Sys", "Ctx", "Earlier discussion summary", [], "Q")
        # system + summary user + summary assistant ack + user question = 4
        assert len(msgs) == 4
        assert "Earlier discussion summary" in msgs[1]["content"]
        assert msgs[2]["role"] == "assistant"

    def test_summary_not_injected_when_none(self):
        msgs = assemble_messages("Sys", "Ctx", None, [], "Q")
        # system + user question = 2
        assert len(msgs) == 2


# ── Prompts: Context Budget ───────────────────────────────────────────────────

class TestContextBudget:
    def test_budget_is_positive(self):
        budget = compute_context_budget(model_context_tokens=8192, response_tokens=1000)
        assert budget > 0

    def test_budget_shrinks_with_larger_response_reserve(self):
        b1 = compute_context_budget(8192, 1000)
        b2 = compute_context_budget(8192, 2000)
        assert b1 > b2

    def test_budget_never_negative(self):
        # Extremely small context window
        budget = compute_context_budget(model_context_tokens=100, response_tokens=5000)
        assert budget == 0


# ── ChatService ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_conversation(db):
    from app.chat.service import ChatService
    from app.llm.service import LLMService

    # Insert a repo first
    from models import Repository
    repo = Repository(url="https://github.com/test/repo", name="test/repo", status="ready")
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    svc = ChatService(db, LLMService())
    conv = await svc.create_conversation(repo.id)

    assert conv.id is not None
    assert conv.repository_id == repo.id
    assert conv.title == "New conversation"


@pytest.mark.asyncio
async def test_list_conversations(db):
    from app.chat.service import ChatService
    from app.llm.service import LLMService
    from models import Repository

    repo = Repository(url="https://github.com/test/repo2", name="test/repo2", status="ready")
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    svc = ChatService(db, LLMService())
    await svc.create_conversation(repo.id)
    await svc.create_conversation(repo.id)

    convs = await svc.list_conversations(repo.id)
    assert len(convs) == 2


@pytest.mark.asyncio
async def test_delete_conversation(db):
    from app.chat.service import ChatService
    from app.llm.service import LLMService
    from models import Repository

    repo = Repository(url="https://github.com/test/repo3", name="test/repo3", status="ready")
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    svc = ChatService(db, LLMService())
    conv = await svc.create_conversation(repo.id)
    await svc.delete_conversation(conv.id)

    convs = await svc.list_conversations(repo.id)
    assert len(convs) == 0


@pytest.mark.asyncio
async def test_send_message_streams_and_persists(db):
    """Full pipeline: send message, verify streaming and DB persistence."""
    from app.chat.service import ChatService
    from app.llm.service import LLMService
    from models import Repository

    repo = Repository(
        url="https://github.com/test/repo4",
        name="test/repo4",
        status="ready",
        metadata_json={"description": "A test repo", "primary_language": "Python"},
    )
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    llm_svc = LLMService()

    async def _mock_stream(messages, config, stream):
        for word in ["Hello", " world", "!"]:
            yield word

    with (
        patch("app.chat.service.retrieve_context", return_value=[]),
        patch.object(llm_svc, "chat_completion", side_effect=_mock_stream),
    ):
        svc = ChatService(db, llm_svc)
        conv = await svc.create_conversation(repo.id)

        stream = await svc.send_message(conv.id, "What does this repo do?")
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

    assert chunks == ["Hello", " world", "!"]

    # Verify messages persisted
    msgs = await svc.get_conversation_history(conv.id)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].content == "What does this repo do?"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == "Hello world!"


@pytest.mark.asyncio
async def test_send_message_sets_has_diff_when_diff_present(db):
    """Verify has_diff=True when assistant response contains a diff block."""
    from app.chat.service import ChatService
    from app.llm.service import LLMService
    from models import Repository

    repo = Repository(
        url="https://github.com/test/repo5",
        name="test/repo5",
        status="ready",
        metadata_json={},
    )
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    diff_response = "Here's the fix:\n```diff\n--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n+import os\n```"

    llm_svc = LLMService()

    async def _mock_stream(messages, config, stream):
        yield diff_response

    with (
        patch("app.chat.service.retrieve_context", return_value=[]),
        patch.object(llm_svc, "chat_completion", side_effect=_mock_stream),
    ):
        svc = ChatService(db, llm_svc)
        conv = await svc.create_conversation(repo.id)
        stream = await svc.send_message(conv.id, "Fix the import")
        async for _ in stream:
            pass

    msgs = await svc.get_conversation_history(conv.id)
    assistant_msg = next(m for m in msgs if m.role == "assistant")
    assert assistant_msg.has_diff is True


@pytest.mark.asyncio
async def test_conversation_auto_titled(db):
    """Verify title is set from first message."""
    from app.chat.service import ChatService
    from app.llm.service import LLMService
    from models import Repository

    repo = Repository(
        url="https://github.com/test/repo6",
        name="test/repo6",
        status="ready",
        metadata_json={},
    )
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    llm_svc = LLMService()

    async def _mock_stream(messages, config, stream):
        yield "answer"

    with (
        patch("app.chat.service.retrieve_context", return_value=[]),
        patch.object(llm_svc, "chat_completion", side_effect=_mock_stream),
    ):
        svc = ChatService(db, llm_svc)
        conv = await svc.create_conversation(repo.id)
        stream = await svc.send_message(conv.id, "Explain the auth module")
        async for _ in stream:
            pass

    await db.refresh(conv)
    assert conv.title == "Explain the auth module"


@pytest.mark.asyncio
async def test_history_included_in_subsequent_messages(db):
    """Verify conversation history is passed to the LLM on subsequent turns."""
    from app.chat.service import ChatService
    from app.llm.service import LLMService
    from models import Repository

    repo = Repository(
        url="https://github.com/test/repo7",
        name="test/repo7",
        status="ready",
        metadata_json={},
    )
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    await db.commit()

    captured_messages = []
    llm_svc = LLMService()

    async def _mock_stream(messages, config, stream):
        captured_messages.clear()
        captured_messages.extend(messages)
        yield "response"

    with (
        patch("app.chat.service.retrieve_context", return_value=[]),
        patch.object(llm_svc, "chat_completion", side_effect=_mock_stream),
    ):
        svc = ChatService(db, llm_svc)
        conv = await svc.create_conversation(repo.id)

        # First message
        stream = await svc.send_message(conv.id, "First question")
        async for _ in stream:
            pass

        # Second message — should include history
        stream = await svc.send_message(conv.id, "Second question")
        async for _ in stream:
            pass

    # The second call's messages should include history from first exchange.
    # Expected structure: [system, user:"First question", assistant:"response", user:"Second question"]
    roles = [m["role"] for m in captured_messages]
    assert "assistant" in roles, "History should include an assistant message from the first exchange"

    contents = [m["content"] for m in captured_messages]
    assert any("First question" in c for c in contents), "History should contain the first user message"
    assert any("response" in c for c in contents), "History should contain the first assistant response"


@pytest.mark.asyncio
async def test_send_message_raises_for_missing_conversation(db):
    from app.chat.service import ChatService
    from app.llm.service import LLMService

    svc = ChatService(db, LLMService())
    with pytest.raises(ValueError, match="not found"):
        await svc.send_message("nonexistent-id", "Hello")
