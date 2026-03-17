"""
Stage 3 tests — Knowledge Base.

Run with:
    pytest tests/test_stage3.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.ingester.schemas import (
    CommitFileChange,
    ExtractedBranch,
    ExtractedCommit,
    ExtractedData,
    ExtractedFile,
    ExtractedIssue,
    ExtractedPR,
    IssueComment,
    PRReviewComment,
    RepoMetadata,
)
from app.knowledge_base.chunkers import (
    chunk_code_file,
    chunk_commit,
    chunk_issue,
    chunk_media_file,
    chunk_pull_request,
    chunk_repo_overview,
)
from app.knowledge_base.schemas import Chunk, RetrievedChunk


# ── Fixtures ──────────────────────────────────────────────────────────────────

REPO_ID = "test-repo-uuid-1234"


def _make_text_file(path: str, content: str, lines: int = 0) -> ExtractedFile:
    if not lines:
        lines = content.count("\n") + 1
    return ExtractedFile(
        path=path,
        extension=path.rsplit(".", 1)[-1] if "." in path else "",
        size_bytes=len(content.encode()),
        content=content,
        is_media_ref=False,
    )


def _make_media_file(path: str, size: int = 45000) -> ExtractedFile:
    return ExtractedFile(
        path=path,
        extension=path.rsplit(".", 1)[-1],
        size_bytes=size,
        content=None,
        is_media_ref=True,
        media_type="image",
    )


def _make_commit(hash_: str = "abc1234567890", message: str = "Fix bug") -> ExtractedCommit:
    return ExtractedCommit(
        hash=hash_,
        short_hash=hash_[:7],
        author_name="Alice",
        author_email="alice@example.com",
        date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        message=message,
        branches=["main"],
        files_changed=[CommitFileChange(path="main.py", added_lines=5, removed_lines=2)],
        diff_preview="--- a/main.py\n+++ b/main.py\n@@ -1,3 +1,3 @@\n-old\n+new",
        is_merge_commit=False,
    )


def _make_issue(number: int = 1, state: str = "open") -> ExtractedIssue:
    return ExtractedIssue(
        number=number,
        title="Button not working",
        body="The submit button does nothing.",
        labels=["bug", "ui"],
        state=state,
        author="bob",
        comments=[
            IssueComment(author="alice", body="Confirmed.", created_at=datetime.now(timezone.utc)),
        ],
        created_at=datetime.now(timezone.utc),
        closed_at=None,
    )


def _make_pr(number: int = 1, is_merged: bool = True) -> ExtractedPR:
    return ExtractedPR(
        number=number,
        title="Add login feature",
        body="Implements OAuth login.",
        state="merged" if is_merged else "open",
        author="carol",
        is_merged=is_merged,
        merged_at=datetime.now(timezone.utc) if is_merged else None,
        review_comments=[
            PRReviewComment(author="dave", body="LGTM", path="auth.py"),
        ],
        created_at=datetime.now(timezone.utc),
    )


# ── Chunker: Code Files ────────────────────────────────────────────────────────

class TestCodeChunker:
    def test_small_file_is_single_chunk(self):
        content = "\n".join(f"line {i}" for i in range(10))
        f = _make_text_file("src/small.py", content)
        chunks = chunk_code_file(f, REPO_ID)
        assert len(chunks) == 1
        assert chunks[0].metadata["type"] == "code"
        assert chunks[0].metadata["file_path"] == "src/small.py"
        assert chunks[0].metadata["repo_id"] == REPO_ID
        assert chunks[0].metadata["line_start"] == 1

    def test_chunk_contains_file_content(self):
        content = "def foo():\n    return 1\n"
        f = _make_text_file("utils.py", content)
        chunks = chunk_code_file(f, REPO_ID)
        assert content in chunks[0].text

    def test_python_file_detects_language(self):
        f = _make_text_file("app.py", "def hello():\n    pass\n")
        chunks = chunk_code_file(f, REPO_ID)
        assert chunks[0].metadata["language"] == "python"

    def test_typescript_file_detects_language(self):
        f = _make_text_file("app.ts", "export function greet() {}\n")
        chunks = chunk_code_file(f, REPO_ID)
        assert chunks[0].metadata["language"] == "typescript"

    def test_unknown_extension_defaults_to_unknown(self):
        f = _make_text_file("Makefile", "all:\n\tmake build\n")
        chunks = chunk_code_file(f, REPO_ID)
        assert chunks[0].metadata["language"] == "unknown"

    def test_large_python_file_splits_by_functions(self):
        # Build a file with multiple distinct function definitions
        funcs = []
        for i in range(10):
            block = f"def func_{i}():\n" + "\n".join(f"    line = {j}" for j in range(60))
            funcs.append(block)
        content = "\n\n".join(funcs)
        f = _make_text_file("big.py", content)
        chunks = chunk_code_file(f, REPO_ID)
        assert len(chunks) > 1

    def test_large_unknown_language_uses_sliding_window(self):
        # A large file with no definition patterns → sliding window
        content = "\n".join(f"line {i}" for i in range(600))
        f = _make_text_file("data.txt", content)
        chunks = chunk_code_file(f, REPO_ID)
        assert len(chunks) > 1
        # Verify overlap: last line of one chunk appears in next
        assert chunks[0].metadata["line_end"] > chunks[1].metadata["line_start"]

    def test_file_with_no_content_returns_empty(self):
        f = ExtractedFile(
            path="empty.py", extension=".py", size_bytes=0,
            content=None, is_media_ref=False,
        )
        chunks = chunk_code_file(f, REPO_ID)
        assert chunks == []

    def test_chunk_ids_are_unique(self):
        content = "\n".join(f"line {i}" for i in range(600))
        f = _make_text_file("long.py", content)
        chunks = chunk_code_file(f, REPO_ID)
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))


# ── Chunker: Media Files ───────────────────────────────────────────────────────

class TestMediaChunker:
    def test_media_chunk_has_correct_type(self):
        f = _make_media_file("assets/logo.png")
        chunks = chunk_media_file(f, REPO_ID)
        assert len(chunks) == 1
        assert chunks[0].metadata["type"] == "media_ref"

    def test_media_chunk_text_contains_path(self):
        f = _make_media_file("assets/logo.png")
        chunks = chunk_media_file(f, REPO_ID)
        assert "assets/logo.png" in chunks[0].text

    def test_media_chunk_text_contains_size(self):
        f = _make_media_file("img.jpg", size=45000)
        chunks = chunk_media_file(f, REPO_ID)
        assert "KB" in chunks[0].text

    def test_media_chunk_metadata_has_file_size(self):
        f = _make_media_file("img.jpg", size=12345)
        chunks = chunk_media_file(f, REPO_ID)
        assert chunks[0].metadata["file_size"] == 12345


# ── Chunker: Commits ──────────────────────────────────────────────────────────

class TestCommitChunker:
    def test_commit_chunk_has_correct_type(self):
        commit = _make_commit()
        chunks = chunk_commit(commit, REPO_ID)
        assert len(chunks) == 1
        assert chunks[0].metadata["type"] == "commit"

    def test_commit_text_contains_hash(self):
        commit = _make_commit("abc1234567890")
        chunks = chunk_commit(commit, REPO_ID)
        assert "abc1234" in chunks[0].text

    def test_commit_text_contains_message(self):
        commit = _make_commit(message="Fix the login bug")
        chunks = chunk_commit(commit, REPO_ID)
        assert "Fix the login bug" in chunks[0].text

    def test_commit_text_contains_diff(self):
        commit = _make_commit()
        chunks = chunk_commit(commit, REPO_ID)
        assert "main.py" in chunks[0].text

    def test_commit_metadata_has_author(self):
        commit = _make_commit()
        chunks = chunk_commit(commit, REPO_ID)
        assert chunks[0].metadata["author"] == "Alice"

    def test_commit_chunk_id_uses_hash(self):
        commit = _make_commit("abc1234567890")
        chunks = chunk_commit(commit, REPO_ID)
        assert "abc1234567890" in chunks[0].id


# ── Chunker: Issues ────────────────────────────────────────────────────────────

class TestIssueChunker:
    def test_issue_chunk_has_correct_type(self):
        issue = _make_issue()
        chunks = chunk_issue(issue, REPO_ID)
        assert len(chunks) == 1
        assert chunks[0].metadata["type"] == "issue"

    def test_issue_text_contains_title(self):
        issue = _make_issue()
        chunks = chunk_issue(issue, REPO_ID)
        assert "Button not working" in chunks[0].text

    def test_issue_text_contains_comments(self):
        issue = _make_issue()
        chunks = chunk_issue(issue, REPO_ID)
        assert "Confirmed." in chunks[0].text

    def test_issue_text_truncated_at_5000_chars(self):
        long_body = "x" * 6000
        issue = ExtractedIssue(
            number=99, title="Long issue", body=long_body,
            labels=[], state="open", author="user",
            comments=[], created_at=datetime.now(timezone.utc), closed_at=None,
        )
        chunks = chunk_issue(issue, REPO_ID)
        assert len(chunks[0].text) <= 5050  # Allow for "[truncated]" suffix

    def test_many_comments_capped_at_6(self):
        comments = [
            IssueComment(author=f"user{i}", body=f"Comment {i}", created_at=datetime.now(timezone.utc))
            for i in range(10)
        ]
        issue = ExtractedIssue(
            number=1, title="Issue", body="body", labels=[], state="open",
            author="x", comments=comments, created_at=datetime.now(timezone.utc), closed_at=None,
        )
        chunks = chunk_issue(issue, REPO_ID)
        # Verify truncation note is present
        assert "showing 6 of 10" in chunks[0].text

    def test_issue_metadata_has_number_and_state(self):
        issue = _make_issue(number=42, state="closed")
        chunks = chunk_issue(issue, REPO_ID)
        assert chunks[0].metadata["number"] == 42
        assert chunks[0].metadata["state"] == "closed"


# ── Chunker: Pull Requests ────────────────────────────────────────────────────

class TestPRChunker:
    def test_pr_chunk_has_correct_type(self):
        pr = _make_pr()
        chunks = chunk_pull_request(pr, REPO_ID)
        assert len(chunks) == 1
        assert chunks[0].metadata["type"] == "pull_request"

    def test_pr_text_contains_title(self):
        pr = _make_pr()
        chunks = chunk_pull_request(pr, REPO_ID)
        assert "Add login feature" in chunks[0].text

    def test_pr_text_contains_review_comments(self):
        pr = _make_pr()
        chunks = chunk_pull_request(pr, REPO_ID)
        assert "LGTM" in chunks[0].text

    def test_pr_metadata_has_is_merged(self):
        pr = _make_pr(is_merged=True)
        chunks = chunk_pull_request(pr, REPO_ID)
        assert chunks[0].metadata["is_merged"] is True


# ── Chunker: Repo Overview ────────────────────────────────────────────────────

class TestRepoOverviewChunker:
    def test_overview_chunk_type(self):
        meta = RepoMetadata(
            description="A test repo", primary_language="Python",
            stars=100, forks=20, topics=["api", "backend"],
            license_name="MIT", default_branch="main", is_private=False,
        )
        chunk = chunk_repo_overview(REPO_ID, "owner/repo", meta, 500, 120)
        assert chunk.metadata["type"] == "repo_overview"

    def test_overview_text_contains_repo_name(self):
        meta = RepoMetadata(
            description="", primary_language="", stars=0, forks=0,
            topics=[], license_name="", default_branch="main", is_private=False,
        )
        chunk = chunk_repo_overview(REPO_ID, "alice/myproject", meta, 0, 0)
        assert "alice/myproject" in chunk.text

    def test_overview_text_contains_stats(self):
        meta = RepoMetadata(
            description="Great repo", primary_language="Go",
            stars=500, forks=30, topics=["cli"],
            license_name="Apache 2.0", default_branch="main", is_private=False,
        )
        chunk = chunk_repo_overview(REPO_ID, "x/y", meta, 1000, 200)
        assert "500" in chunk.text  # stars
        assert "1000" in chunk.text  # commits
        assert "200" in chunk.text   # files


# ── KB Service (mocked ChromaDB + embeddings) ─────────────────────────────────

@pytest.mark.asyncio
async def test_build_knowledge_base_calls_store(db):
    """Verify build_knowledge_base chunks all data types and calls store.add_documents."""
    from app.knowledge_base.service import build_knowledge_base

    data = ExtractedData(
        repo_id=REPO_ID,
        repo_name="owner/repo",
        metadata=RepoMetadata(
            description="Test", primary_language="Python",
            stars=0, forks=0, topics=[], license_name="",
            default_branch="main", is_private=False,
        ),
        files=[_make_text_file("main.py", "def hello():\n    pass\n")],
        commits=[_make_commit()],
        issues=[_make_issue()],
        pull_requests=[_make_pr()],
        branches=[ExtractedBranch(name="main", head_commit_hash="abc123")],
    )

    with (
        patch("app.knowledge_base.service.embedding_service.embed_texts", return_value=[[0.1] * 384]),
        patch("app.knowledge_base.store.create_collection"),
        patch("app.knowledge_base.store.get_stored_ids", return_value=set()),
        patch("app.knowledge_base.store.add_documents") as mock_add,
        patch("app.knowledge_base.store.collection_count", return_value=5),
    ):
        await build_knowledge_base(REPO_ID, data, db)
        assert mock_add.call_count >= 1


@pytest.mark.asyncio
async def test_build_knowledge_base_resumes_from_checkpoint(db):
    """Verify build skips already-stored chunks when resuming."""
    from app.knowledge_base.service import build_knowledge_base
    from checkpoint import CheckpointManager

    # Save a checkpoint indicating batch 0 is done
    cp_mgr = CheckpointManager(db)
    await cp_mgr.save(
        repo_id=REPO_ID,
        operation="kb_build",
        stage="embedding_chunks",
        progress_current=1,
        progress_total=5,
        state={"total_chunks": 5, "chunks_stored": 1, "last_batch_index": 0},
    )
    await db.commit()

    data = ExtractedData(
        repo_id=REPO_ID,
        repo_name="owner/repo",
        metadata=RepoMetadata(
            description="", primary_language="", stars=0, forks=0,
            topics=[], license_name="", default_branch="main", is_private=False,
        ),
        files=[_make_text_file("a.py", "x=1\n")],
        commits=[_make_commit("hash001")],
        issues=[], pull_requests=[],
        branches=[],
    )

    embed_calls = []

    def mock_embed(texts):
        embed_calls.extend(texts)
        return [[0.1] * 384] * len(texts)

    with (
        patch("app.knowledge_base.service.embedding_service.embed_texts", side_effect=mock_embed),
        patch("app.knowledge_base.store.create_collection"),
        patch("app.knowledge_base.store.get_stored_ids", return_value=set()),
        patch("app.knowledge_base.store.add_documents") as mock_add,
        patch("app.knowledge_base.store.collection_count", return_value=2),
    ):
        await build_knowledge_base(REPO_ID, data, db)
        # With batch 0 skipped and only 3 chunks total (code + commit + overview),
        # everything is in batch 0 which was checkpointed as done.
        # Therefore add_documents should NOT be called (all batches skipped).
        assert mock_add.call_count == 0


# ── Retriever: distance → score ───────────────────────────────────────────────

class TestDistanceToScore:
    def test_zero_distance_gives_max_score(self):
        from app.knowledge_base.retriever import _distance_to_score
        assert _distance_to_score(0.0) == 1.0

    def test_max_distance_gives_zero_score(self):
        from app.knowledge_base.retriever import _distance_to_score, _MAX_DISTANCE
        assert _distance_to_score(_MAX_DISTANCE) == 0.0

    def test_beyond_max_distance_clamps_to_zero(self):
        from app.knowledge_base.retriever import _distance_to_score
        assert _distance_to_score(5.0) == 0.0

    def test_mid_distance_gives_mid_score(self):
        from app.knowledge_base.retriever import _distance_to_score, _MAX_DISTANCE
        score = _distance_to_score(_MAX_DISTANCE / 2)
        assert 0.45 < score < 0.55  # ~0.5


# ── Retriever: integration ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retriever_calls_embed_and_query():
    """Verify retrieve() embeds the query and calls ChromaDB."""
    from app.knowledge_base.retriever import retrieve

    mock_results = [{
        "id": "commit_abc123",
        "document": "Commit: abc123 ...",
        "metadata": {"type": "commit", "repo_id": REPO_ID},
        "distance": 0.3,
    }]

    with (
        patch("app.knowledge_base.retriever.embedding_service.embed_query", return_value=[0.1] * 384),
        patch("app.knowledge_base.retriever.store.query", return_value=mock_results),
    ):
        results = await retrieve(REPO_ID, "what changed in the last commit?")
        assert len(results) == 1
        assert results[0].chunk_id == "commit_abc123"
        assert results[0].similarity_score > 0


@pytest.mark.asyncio
async def test_retriever_filters_by_content_type():
    """Verify content_types filter is passed to ChromaDB."""
    from app.knowledge_base.retriever import retrieve

    with (
        patch("app.knowledge_base.retriever.embedding_service.embed_query", return_value=[0.1] * 384),
        patch("app.knowledge_base.retriever.store.query", return_value=[]) as mock_query,
    ):
        await retrieve(REPO_ID, "find code", content_types=["code"])
        call_kwargs = mock_query.call_args
        where_arg = call_kwargs[0][3]  # 4th positional arg is `where`
        assert where_arg == {"type": "code"}


@pytest.mark.asyncio
async def test_retriever_multi_type_filter():
    """Verify multiple content_types uses $in operator."""
    from app.knowledge_base.retriever import retrieve

    with (
        patch("app.knowledge_base.retriever.embedding_service.embed_query", return_value=[0.1] * 384),
        patch("app.knowledge_base.retriever.store.query", return_value=[]) as mock_query,
    ):
        await retrieve(REPO_ID, "search", content_types=["code", "commit"])
        call_kwargs = mock_query.call_args
        where_arg = call_kwargs[0][3]
        assert where_arg == {"type": {"$in": ["code", "commit"]}}
