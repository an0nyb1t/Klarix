"""
Git service — clones repositories and extracts code, commits, and branches.

All operations that touch the filesystem run synchronously (GitPython is sync).
Callers in the async service.py must wrap calls using asyncio.to_thread().
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import git
from git.exc import GitCommandError

from app.ingester.schemas import (
    CommitFileChange,
    ExtractedBranch,
    ExtractedCommit,
    ExtractedFile,
)

logger = logging.getLogger(__name__)

# Extensions that get reference-only entries (no content stored)
MEDIA_EXTENSIONS = {
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp", ".tiff",
    # Video
    ".mp4", ".mp3", ".wav", ".ogg", ".webm", ".mov", ".avi", ".mkv", ".flac",
    # Fonts
    ".ttf", ".woff", ".woff2", ".eot", ".otf",
    # Archives
    ".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz",
    # Compiled / binary
    ".pdf", ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".o", ".a",
    ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
}

MEDIA_TYPE_MAP = {
    frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp", ".tiff"}): "image",
    frozenset({".mp4", ".mp3", ".wav", ".ogg", ".webm", ".mov", ".avi", ".mkv", ".flac"}): "audio/video",
    frozenset({".ttf", ".woff", ".woff2", ".eot", ".otf"}): "font",
    frozenset({".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz"}): "archive",
}

MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_COMMITS = 10_000
DIFF_PREVIEW_CHARS = 3_000
BINARY_DETECT_BYTES = 8_192


def _get_media_type(extension: str) -> str:
    for extensions, media_type in MEDIA_TYPE_MAP.items():
        if extension in extensions:
            return media_type
    return "binary"


def _is_media_file(path: str) -> bool:
    ext = Path(path).suffix.lower()
    return ext in MEDIA_EXTENSIONS


def _is_binary_content(data: bytes) -> bool:
    """Detect binary files by looking for null bytes in the first 8KB."""
    return b"\x00" in data[:BINARY_DETECT_BYTES]


class GitServiceError(Exception):
    pass


class RepoTooLargeError(GitServiceError):
    pass


def clone_repository(clone_url: str, clone_path: str) -> git.Repo:
    """
    Clone a repository using --mirror (full history, all branches).
    If a clone already exists at clone_path, return it directly.
    Raises GitServiceError on failure.
    """
    if os.path.exists(clone_path):
        logger.info("Repo already cloned at %s, opening existing.", clone_path)
        return git.Repo(clone_path)

    os.makedirs(os.path.dirname(clone_path), exist_ok=True)

    logger.info("Cloning %s → %s", clone_url, clone_path)
    try:
        repo = git.Repo.clone_from(
            clone_url,
            clone_path,
            mirror=True,
        )
        return repo
    except GitCommandError as e:
        msg = str(e)
        if "Repository not found" in msg or "not found" in msg.lower():
            raise GitServiceError("Repository not found. Check the URL and your token.")
        if "Authentication failed" in msg or "could not read" in msg.lower():
            raise GitServiceError(
                "Authentication failed. Provide a valid GitHub token for private repos."
            )
        raise GitServiceError(f"Git clone failed: {e.stderr.strip() if e.stderr else str(e)}")


def fetch_updates(clone_path: str) -> None:
    """Fetch all updates for an existing mirror clone (for re-sync)."""
    try:
        repo = git.Repo(clone_path)
        repo.git.fetch("--all", "--prune")
    except GitCommandError as e:
        raise GitServiceError(f"Git fetch failed: {e.stderr.strip() if e.stderr else str(e)}")


def extract_branches(repo: git.Repo) -> list[ExtractedBranch]:
    """Return all branches with their HEAD commit hash."""
    branches = []
    for ref in repo.references:
        # In a mirror clone, all branch refs are in refs/heads/
        name = ref.name
        if name.startswith("refs/heads/"):
            name = name[len("refs/heads/"):]
        elif name.startswith("HEAD"):
            continue
        try:
            branches.append(ExtractedBranch(
                name=name,
                head_commit_hash=ref.commit.hexsha,
            ))
        except Exception:
            continue
    return branches


def extract_commits(
    repo: git.Repo,
    progress_callback=None,
    since_hash: str | None = None,
) -> list[ExtractedCommit]:
    """
    Extract all commits across all branches, up to MAX_COMMITS.
    If since_hash is given, only extract commits newer than that hash (for re-sync).
    Raises RepoTooLargeError if commit count exceeds MAX_COMMITS.
    """
    # Map each commit to the branches it appears on.
    # Use rev-list per branch (stops at merge base) for better performance
    # than iterating full history per branch.
    commit_branches: dict[str, list[str]] = {}
    branch_refs = []
    for ref in repo.references:
        ref_name = ref.name
        if ref_name.startswith("refs/heads/"):
            branch_refs.append((ref_name[len("refs/heads/"):], ref))
        elif not ref_name.startswith("HEAD"):
            branch_refs.append((ref_name, ref))

    for branch_name, ref in branch_refs:
        try:
            for commit in repo.iter_commits(ref):
                if commit.hexsha not in commit_branches:
                    commit_branches[commit.hexsha] = []
                commit_branches[commit.hexsha].append(branch_name)
        except Exception:
            continue

    # Get unique commits ordered by date (most recent first)
    try:
        all_commits = list(repo.iter_commits("--all"))
    except Exception as e:
        raise GitServiceError(f"Failed to iterate commits: {e}")

    total = len(all_commits)
    if total > MAX_COMMITS and since_hash is None:
        raise RepoTooLargeError(
            f"Repository has {total:,} commits which exceeds the V1 limit of "
            f"{MAX_COMMITS:,}. Try again with a smaller repository."
        )

    # If re-syncing, stop at the known hash
    if since_hash:
        cutoff = next((i for i, c in enumerate(all_commits) if c.hexsha == since_hash), None)
        if cutoff is not None:
            all_commits = all_commits[:cutoff]

    extracted = []
    for i, commit in enumerate(all_commits):
        if progress_callback:
            progress_callback(i + 1, total)

        files_changed = []
        diff_preview = ""
        is_merge = len(commit.parents) > 1

        try:
            if commit.stats.files:
                for path, stats in commit.stats.files.items():
                    files_changed.append(CommitFileChange(
                        path=path,
                        added_lines=stats.get("insertions", 0),
                        removed_lines=stats.get("deletions", 0),
                    ))
        except Exception:
            pass

        # Get diff preview (skip for merge commits — too noisy)
        if not is_merge and commit.parents:
            try:
                diff_text = repo.git.diff(
                    commit.parents[0].hexsha,
                    commit.hexsha,
                    "--unified=2",
                )
                diff_preview = diff_text[:DIFF_PREVIEW_CHARS]
            except Exception:
                pass
        elif not commit.parents:
            # Initial commit
            try:
                diff_text = repo.git.show(commit.hexsha, "--unified=2", "--format=")
                diff_preview = diff_text[:DIFF_PREVIEW_CHARS]
            except Exception:
                pass

        commit_dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)

        extracted.append(ExtractedCommit(
            hash=commit.hexsha,
            short_hash=commit.hexsha[:7],
            author_name=commit.author.name or "",
            author_email=commit.author.email or "",
            date=commit_dt,
            message=commit.message.strip(),
            branches=commit_branches.get(commit.hexsha, []),
            files_changed=files_changed,
            diff_preview=diff_preview,
            is_merge_commit=is_merge,
        ))

    return extracted


def extract_files(
    repo: git.Repo,
    default_branch: str,
    progress_callback=None,
) -> list[ExtractedFile]:
    """
    Extract all files from the default branch.
    - Text files: content included
    - Media/binary files: reference-only entry (no content)
    - Files >1MB: skipped entirely
    """
    try:
        head_commit = repo.commit(default_branch)
    except Exception:
        # Fallback: try common branch names
        for branch_name in ("main", "master", "HEAD"):
            try:
                head_commit = repo.commit(branch_name)
                break
            except Exception:
                continue
        else:
            logger.warning("Could not resolve default branch '%s'", default_branch)
            return []

    blobs = [item for item in head_commit.tree.traverse() if item.type == "blob"]
    total = len(blobs)
    extracted = []

    for i, blob in enumerate(blobs):
        if progress_callback:
            progress_callback(i + 1, total)

        path = blob.path
        ext = Path(path).suffix.lower()
        size = blob.data_stream.size

        # Skip files larger than 1MB
        if size > MAX_FILE_SIZE:
            logger.debug("Skipping large file: %s (%d bytes)", path, size)
            continue

        # Media files → reference only
        if _is_media_file(path):
            extracted.append(ExtractedFile(
                path=path,
                extension=ext,
                size_bytes=size,
                content=None,
                is_media_ref=True,
                media_type=_get_media_type(ext),
            ))
            continue

        # Read content and check for binary
        try:
            raw = blob.data_stream.read()
        except Exception as e:
            logger.debug("Could not read %s: %s", path, e)
            continue

        if _is_binary_content(raw):
            # Binary file detected — treat as reference
            extracted.append(ExtractedFile(
                path=path,
                extension=ext,
                size_bytes=size,
                content=None,
                is_media_ref=True,
                media_type="binary",
            ))
            continue

        try:
            content = raw.decode("utf-8", errors="replace")
        except Exception:
            continue

        extracted.append(ExtractedFile(
            path=path,
            extension=ext,
            size_bytes=size,
            content=content,
            is_media_ref=False,
        ))

    return extracted
