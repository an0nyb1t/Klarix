"""
Chunking logic for each data type.

Each chunker returns a list of Chunk objects ready for embedding.
Chunking strategy:
  - Code: logical boundaries (functions/classes), fallback sliding window
  - Media refs: single metadata-only chunk
  - Commits: message + file changes + diff preview
  - Issues/PRs: title + body + comments (truncated to 5000 chars)
  - Repo overview: auto-generated summary chunk
"""

import re
from pathlib import Path

from app.ingester.schemas import (
    ExtractedCommit,
    ExtractedFile,
    ExtractedIssue,
    ExtractedPR,
    RepoMetadata,
)
from app.knowledge_base.schemas import Chunk

# Language → regex pattern that matches top-level definition lines
_DEFINITION_PATTERNS: dict[str, re.Pattern] = {
    "python": re.compile(r"^(def |class )", re.MULTILINE),
    "javascript": re.compile(r"^(function |class |const \w+ = |export (default |function |class ))", re.MULTILINE),
    "typescript": re.compile(r"^(function |class |const \w+ = |export (default |function |class |interface |type ))", re.MULTILINE),
    "java": re.compile(r"^(public |private |protected |static |abstract |class |interface )", re.MULTILINE),
    "go": re.compile(r"^func ", re.MULTILINE),
    "rust": re.compile(r"^(pub |fn |struct |impl |enum |trait )", re.MULTILINE),
    "ruby": re.compile(r"^(def |class |module )", re.MULTILINE),
    "php": re.compile(r"^(function |class |interface |trait )", re.MULTILINE),
    "c": re.compile(r"^\w[\w\s\*]+\(", re.MULTILINE),
    "cpp": re.compile(r"^\w[\w\s\*:<>]+\(", re.MULTILINE),
    "csharp": re.compile(r"^(public |private |protected |static |abstract |class |interface |namespace |enum )", re.MULTILINE),
}

_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
}

SMALL_FILE_LINE_LIMIT = 500
WINDOW_SIZE = 200
WINDOW_OVERLAP = 40
ISSUE_MAX_CHARS = 5000


def _detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    return _EXT_TO_LANGUAGE.get(ext, "unknown")


def chunk_code_file(file: ExtractedFile, repo_id: str) -> list[Chunk]:
    """Chunk a text code file by logical boundaries."""
    if file.content is None:
        return []

    lines = file.content.splitlines()
    total_lines = len(lines)
    language = _detect_language(file.path)
    safe_path = file.path.replace("/", "_").replace(".", "_")

    # Small files: single chunk
    if total_lines <= SMALL_FILE_LINE_LIMIT:
        return [Chunk(
            id=f"code_{safe_path}_1_{total_lines}",
            text=file.content,
            metadata={
                "type": "code",
                "file_path": file.path,
                "language": language,
                "repo_id": repo_id,
                "line_start": 1,
                "line_end": total_lines,
            },
        )]

    # Larger files: split by top-level definitions
    pattern = _DEFINITION_PATTERNS.get(language)
    chunks = []

    if pattern:
        # Find line numbers of definition starts
        split_lines = [0]  # Start at line 0 (index)
        for m in pattern.finditer(file.content):
            line_num = file.content[:m.start()].count("\n")
            if line_num > split_lines[-1] + 5:  # Minimum 5-line sections
                split_lines.append(line_num)
        split_lines.append(total_lines)

        for i in range(len(split_lines) - 1):
            start = split_lines[i]
            end = split_lines[i + 1]
            section = "\n".join(lines[start:end])
            if section.strip():
                chunks.append(Chunk(
                    id=f"code_{safe_path}_{start + 1}_{end}",
                    text=section,
                    metadata={
                        "type": "code",
                        "file_path": file.path,
                        "language": language,
                        "repo_id": repo_id,
                        "line_start": start + 1,
                        "line_end": end,
                    },
                ))

    if not chunks:
        # Fallback: sliding window
        start = 0
        while start < total_lines:
            end = min(start + WINDOW_SIZE, total_lines)
            section = "\n".join(lines[start:end])
            if section.strip():
                chunks.append(Chunk(
                    id=f"code_{safe_path}_{start + 1}_{end}",
                    text=section,
                    metadata={
                        "type": "code",
                        "file_path": file.path,
                        "language": language,
                        "repo_id": repo_id,
                        "line_start": start + 1,
                        "line_end": end,
                    },
                ))
            start += WINDOW_SIZE - WINDOW_OVERLAP

    return chunks


def chunk_media_file(file: ExtractedFile, repo_id: str) -> list[Chunk]:
    """Create a reference-only chunk for a media/binary file."""
    size_kb = file.size_bytes / 1024
    media_type = file.media_type or "binary"
    text = (
        f"[Media File] {file.path}\n"
        f"Type: {media_type}\n"
        f"Size: {size_kb:.1f} KB\n"
        f"Path: {file.path}"
    )
    safe_path = file.path.replace("/", "_").replace(".", "_")
    return [Chunk(
        id=f"media_{safe_path}",
        text=text,
        metadata={
            "type": "media_ref",
            "file_path": file.path,
            "file_size": file.size_bytes,
            "media_type": media_type,
            "repo_id": repo_id,
        },
    )]


def chunk_commit(commit: ExtractedCommit, repo_id: str) -> list[Chunk]:
    """Create one chunk per commit with message + file changes + diff."""
    date_str = commit.date.strftime("%Y-%m-%d") if commit.date else "unknown"
    branch_str = ", ".join(commit.branches[:3]) if commit.branches else "unknown"

    file_lines = []
    for fc in commit.files_changed[:20]:  # Cap at 20 files listed
        file_lines.append(f"  - {fc.path} (+{fc.added_lines} -{fc.removed_lines})")

    parts = [
        f"Commit: {commit.short_hash} by {commit.author_name} on {date_str}",
        f"Branch: {branch_str}",
        "",
        commit.message,
    ]

    if file_lines:
        parts.append("\nFiles changed:")
        parts.extend(file_lines)

    if commit.diff_preview:
        parts.append("\nDiff:")
        parts.append(commit.diff_preview)

    return [Chunk(
        id=f"commit_{commit.hash}",
        text="\n".join(parts),
        metadata={
            "type": "commit",
            "commit_hash": commit.hash,
            "short_hash": commit.short_hash,
            "author": commit.author_name,
            "date": date_str,
            "is_merge": commit.is_merge_commit,
            "repo_id": repo_id,
        },
    )]


def chunk_issue(issue: ExtractedIssue, repo_id: str) -> list[Chunk]:
    """Create one chunk per issue including comments (truncated to 5000 chars)."""
    label_str = ", ".join(issue.labels) if issue.labels else "none"

    parts = [
        f"Issue #{issue.number}: {issue.title} [{issue.state}]",
        f"Labels: {label_str}",
        f"Author: {issue.author}",
        "",
        issue.body or "(no description)",
    ]

    if issue.comments:
        parts.append("\n---\nComments:")
        # Keep first 3 + last 3 comments if many
        display_comments = issue.comments
        if len(issue.comments) > 6:
            display_comments = issue.comments[:3] + issue.comments[-3:]
            parts.append(f"(showing 6 of {len(issue.comments)} comments)")

        for c in display_comments:
            parts.append(f"{c.author}: {c.body}")

    text = "\n".join(parts)
    if len(text) > ISSUE_MAX_CHARS:
        text = text[:ISSUE_MAX_CHARS] + "\n...[truncated]"

    return [Chunk(
        id=f"issue_{issue.number}",
        text=text,
        metadata={
            "type": "issue",
            "number": issue.number,
            "title": issue.title,
            "state": issue.state,
            "repo_id": repo_id,
        },
    )]


def chunk_pull_request(pr: ExtractedPR, repo_id: str) -> list[Chunk]:
    """Create one chunk per PR including review comments (truncated to 5000 chars)."""
    parts = [
        f"Pull Request #{pr.number}: {pr.title} [{pr.state}]",
        f"Author: {pr.author}",
        f"Merged: {'Yes' if pr.is_merged else 'No'}",
        "",
        pr.body or "(no description)",
    ]

    if pr.review_comments:
        parts.append("\n---\nReview Comments:")
        display = pr.review_comments[:6]
        if len(pr.review_comments) > 6:
            parts.append(f"(showing 6 of {len(pr.review_comments)} review comments)")
        for rc in display:
            parts.append(f"{rc.author} on {rc.path}: {rc.body}")

    text = "\n".join(parts)
    if len(text) > ISSUE_MAX_CHARS:
        text = text[:ISSUE_MAX_CHARS] + "\n...[truncated]"

    return [Chunk(
        id=f"pr_{pr.number}",
        text=text,
        metadata={
            "type": "pull_request",
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "is_merged": pr.is_merged,
            "repo_id": repo_id,
        },
    )]


def chunk_repo_overview(
    repo_id: str,
    repo_name: str,
    metadata: RepoMetadata,
    total_commits: int,
    total_files: int,
) -> Chunk:
    """Create one synthetic overview chunk summarizing the repository."""
    topics_str = ", ".join(metadata.topics) if metadata.topics else "none"
    text = (
        f"Repository: {repo_name}\n"
        f"Description: {metadata.description or 'No description'}\n"
        f"Language: {metadata.primary_language or 'unknown'}\n"
        f"Stars: {metadata.stars} | Forks: {metadata.forks}\n"
        f"Topics: {topics_str}\n"
        f"Default branch: {metadata.default_branch}\n"
        f"Total commits: {total_commits} | Total files: {total_files}\n"
        f"License: {metadata.license_name or 'unknown'}\n"
        f"Visibility: {'private' if metadata.is_private else 'public'}"
    )
    return Chunk(
        id=f"repo_overview_{repo_id}",
        text=text,
        metadata={
            "type": "repo_overview",
            "repo_id": repo_id,
            "repo_name": repo_name,
        },
    )
