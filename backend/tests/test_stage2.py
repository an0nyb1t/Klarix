"""
Stage 2 tests — Repo Ingester.

Run with:
    pytest tests/test_stage2.py -v
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import git
import pytest
import pytest_asyncio

from app.ingester.git_service import (
    MEDIA_EXTENSIONS,
    _is_binary_content,
    _is_media_file,
    extract_branches,
    extract_commits,
    extract_files,
)
from app.ingester.schemas import IngestionProgress
from app.ingester.url_parser import InvalidGitHubURL, ParsedRepoURL, parse_github_url


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_git_repo():
    """Create a real temporary git repo with commits for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = git.Repo.init(tmpdir)

        # Configure git identity for the test repo
        repo.config_writer().set_value("user", "name", "Test User").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()

        # Create some files
        files = {
            "main.py": "def hello():\n    print('hello')\n",
            "README.md": "# Test Repo\nA test repository.\n",
            "src/utils.py": "def add(a, b):\n    return a + b\n",
            "assets/logo.png": b"\x89PNG fake binary content",
            "data.bin": b"\x00\x01\x02\x03 binary data",
        }

        # First commit
        os.makedirs(os.path.join(tmpdir, "src"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "assets"), exist_ok=True)

        for path, content in files.items():
            full_path = os.path.join(tmpdir, path)
            mode = "wb" if isinstance(content, bytes) else "w"
            with open(full_path, mode) as f:
                f.write(content)

        repo.index.add(["main.py", "README.md", "src/utils.py", "assets/logo.png", "data.bin"])
        repo.index.commit("Initial commit")

        # Second commit
        with open(os.path.join(tmpdir, "main.py"), "w") as f:
            f.write("def hello():\n    print('hello world')\n\ndef goodbye():\n    print('bye')\n")

        repo.index.add(["main.py"])
        repo.index.commit("Update main.py")

        yield repo, tmpdir


# ── URL Parser ────────────────────────────────────────────────────────────────

class TestURLParser:
    def test_standard_https_url(self):
        result = parse_github_url("https://github.com/owner/repo")
        assert result.owner == "owner"
        assert result.repo == "repo"
        assert result.full_name == "owner/repo"
        assert result.canonical_url == "https://github.com/owner/repo"

    def test_url_with_git_suffix(self):
        result = parse_github_url("https://github.com/owner/repo.git")
        assert result.owner == "owner"
        assert result.repo == "repo"

    def test_url_without_protocol(self):
        result = parse_github_url("github.com/owner/repo")
        assert result.owner == "owner"
        assert result.repo == "repo"

    def test_url_with_branch(self):
        result = parse_github_url("https://github.com/owner/repo/tree/main")
        assert result.owner == "owner"
        assert result.repo == "repo"

    def test_url_with_issues_path(self):
        result = parse_github_url("https://github.com/owner/repo/issues")
        assert result.owner == "owner"
        assert result.repo == "repo"

    def test_clone_url(self):
        result = parse_github_url("https://github.com/owner/repo")
        assert result.clone_url == "https://github.com/owner/repo.git"

    def test_clone_url_with_token(self):
        result = parse_github_url("https://github.com/owner/repo")
        url = result.clone_url_with_token("mytoken")
        assert url == "https://mytoken@github.com/owner/repo.git"

    def test_real_repo_urls(self):
        urls = [
            ("https://github.com/D4Vinci/Scrapling", "D4Vinci", "Scrapling"),
            ("https://github.com/fastapi/fastapi.git", "fastapi", "fastapi"),
            ("https://github.com/microsoft/vscode/tree/main", "microsoft", "vscode"),
        ]
        for url, expected_owner, expected_repo in urls:
            result = parse_github_url(url)
            assert result.owner == expected_owner
            assert result.repo == expected_repo

    def test_empty_url_raises(self):
        with pytest.raises(InvalidGitHubURL):
            parse_github_url("")

    def test_non_github_url_raises(self):
        with pytest.raises(InvalidGitHubURL):
            parse_github_url("https://gitlab.com/owner/repo")

    def test_no_repo_name_raises(self):
        with pytest.raises(InvalidGitHubURL):
            parse_github_url("https://github.com/owner")

    def test_arbitrary_string_raises(self):
        with pytest.raises(InvalidGitHubURL):
            parse_github_url("not a url at all")


# ── Media / Binary Detection ──────────────────────────────────────────────────

class TestMediaDetection:
    def test_image_extensions_are_media(self):
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"]:
            assert _is_media_file(f"assets/image{ext}"), f"{ext} should be detected as media"

    def test_video_audio_are_media(self):
        for ext in [".mp4", ".mp3", ".wav", ".ogg"]:
            assert _is_media_file(f"file{ext}")

    def test_font_files_are_media(self):
        for ext in [".ttf", ".woff", ".woff2", ".eot"]:
            assert _is_media_file(f"font{ext}")

    def test_archive_files_are_media(self):
        for ext in [".zip", ".tar", ".gz", ".rar"]:
            assert _is_media_file(f"archive{ext}")

    def test_compiled_binaries_are_media(self):
        for ext in [".exe", ".dll", ".so", ".dylib", ".pyc"]:
            assert _is_media_file(f"file{ext}")

    def test_text_files_are_not_media(self):
        for path in ["main.py", "README.md", "config.yaml", "index.js", "style.css"]:
            assert not _is_media_file(path), f"{path} should NOT be media"

    def test_binary_content_detection_null_bytes(self):
        assert _is_binary_content(b"normal text" + b"\x00" + b"more text")

    def test_text_content_not_binary(self):
        assert not _is_binary_content(b"def hello():\n    print('hi')\n")

    def test_binary_detection_checks_first_8kb_only(self):
        # File with null byte only after 8KB should not be detected as binary
        safe_content = b"a" * (8192 + 10) + b"\x00"
        assert not _is_binary_content(safe_content)


# ── Git Service ───────────────────────────────────────────────────────────────

class TestGitService:
    def test_extract_branches(self, temp_git_repo):
        repo, _ = temp_git_repo
        branches = extract_branches(repo)
        assert len(branches) >= 1
        branch_names = [b.name for b in branches]
        # At least one of these common names should be present
        assert any(n in branch_names for n in ("main", "master"))

    def test_extract_commits(self, temp_git_repo):
        repo, _ = temp_git_repo
        commits = extract_commits(repo)
        assert len(commits) == 2

        # Most recent commit first
        assert "Update main.py" in commits[0].message
        assert "Initial commit" in commits[1].message

    def test_commits_have_required_fields(self, temp_git_repo):
        repo, _ = temp_git_repo
        commits = extract_commits(repo)
        c = commits[0]
        assert c.hash
        assert len(c.hash) == 40
        assert c.short_hash
        assert len(c.short_hash) == 7
        assert c.author_name
        assert isinstance(c.date, datetime)
        assert c.message

    def test_extract_files_text_content(self, temp_git_repo):
        repo, tmpdir = temp_git_repo
        branches = extract_branches(repo)
        branch_name = branches[0].name
        files = extract_files(repo, branch_name)

        text_files = [f for f in files if not f.is_media_ref]
        paths = [f.path for f in text_files]
        assert "main.py" in paths
        assert "README.md" in paths
        assert "src/utils.py" in paths

    def test_extract_files_skips_media_content(self, temp_git_repo):
        repo, _ = temp_git_repo
        branches = extract_branches(repo)
        branch_name = branches[0].name
        files = extract_files(repo, branch_name)

        media_files = [f for f in files if f.is_media_ref]
        media_paths = [f.path for f in media_files]
        assert "assets/logo.png" in media_paths

        # Media files must have no content
        for mf in media_files:
            assert mf.content is None
            assert mf.is_media_ref is True

    def test_extract_files_binary_becomes_ref(self, temp_git_repo):
        repo, _ = temp_git_repo
        branches = extract_branches(repo)
        branch_name = branches[0].name
        files = extract_files(repo, branch_name)

        binary_file = next((f for f in files if f.path == "data.bin"), None)
        assert binary_file is not None
        assert binary_file.is_media_ref is True
        assert binary_file.content is None

    def test_commit_progress_callback(self, temp_git_repo):
        repo, _ = temp_git_repo
        calls = []

        def cb(current, total):
            calls.append((current, total))

        extract_commits(repo, progress_callback=cb)
        assert len(calls) > 0
        assert calls[-1][0] == calls[-1][1]  # final call: current == total

    def test_file_progress_callback(self, temp_git_repo):
        repo, _ = temp_git_repo
        branches = extract_branches(repo)
        calls = []

        def cb(current, total):
            calls.append((current, total))

        extract_files(repo, branches[0].name, progress_callback=cb)
        assert len(calls) > 0


# ── Schemas ───────────────────────────────────────────────────────────────────

class TestSchemas:
    def test_ingestion_progress_defaults(self):
        p = IngestionProgress(stage="cloning", current=0, total=-1, message="Cloning...")
        assert p.resets_at is None
        assert p.paused_reason == ""

    def test_ingestion_progress_with_pause(self):
        resets = datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
        p = IngestionProgress(
            stage="paused", current=45, total=87,
            message="Rate limit hit",
            resets_at=resets,
            paused_reason="github_rate_limit",
        )
        assert p.resets_at == resets
        assert p.paused_reason == "github_rate_limit"


# ── Service (mocked) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_rejects_invalid_url(db):
    from app.ingester.service import ingest_repository
    with pytest.raises(ValueError, match="not a valid GitHub"):
        await ingest_repository("https://gitlab.com/owner/repo", db)


@pytest.mark.asyncio
async def test_progress_store_is_updated(db):
    from app.ingester import service as svc

    # Directly test the progress store
    svc._set_progress("repo-123", "cloning", 0, -1, "Cloning...")
    progress = svc.get_progress("repo-123")
    assert progress is not None
    assert progress.stage == "cloning"
    assert progress.message == "Cloning..."

    # Update it
    svc._set_progress("repo-123", "extracting_commits", 50, 200, "50/200")
    progress = svc.get_progress("repo-123")
    assert progress.stage == "extracting_commits"
    assert progress.current == 50
