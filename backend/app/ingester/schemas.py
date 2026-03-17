"""
Pydantic models for data extracted by the ingester.
These are passed to the knowledge_base module for chunking and embedding.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExtractedFile:
    path: str
    extension: str
    size_bytes: int
    # None for media/binary refs — content is not stored
    content: str | None
    # True when file is a media/binary reference (no content stored)
    is_media_ref: bool = False
    media_type: str = ""  # "image", "video", "audio", "font", "archive", "binary"


@dataclass
class CommitFileChange:
    path: str
    added_lines: int
    removed_lines: int


@dataclass
class ExtractedCommit:
    hash: str
    short_hash: str
    author_name: str
    author_email: str
    date: datetime
    message: str
    branches: list[str]
    files_changed: list[CommitFileChange]
    # Truncated diff — first 3000 chars only
    diff_preview: str
    is_merge_commit: bool


@dataclass
class ExtractedBranch:
    name: str
    head_commit_hash: str


@dataclass
class IssueComment:
    author: str
    body: str
    created_at: datetime


@dataclass
class ExtractedIssue:
    number: int
    title: str
    body: str
    labels: list[str]
    state: str  # open | closed
    author: str
    comments: list[IssueComment]
    created_at: datetime
    closed_at: datetime | None


@dataclass
class PRReviewComment:
    author: str
    body: str
    path: str | None


@dataclass
class ExtractedPR:
    number: int
    title: str
    body: str
    state: str  # open | closed | merged
    author: str
    is_merged: bool
    merged_at: datetime | None
    review_comments: list[PRReviewComment]
    created_at: datetime


@dataclass
class RepoMetadata:
    description: str
    primary_language: str
    stars: int
    forks: int
    topics: list[str]
    license_name: str
    default_branch: str
    is_private: bool


@dataclass
class ExtractedData:
    """Complete extracted data for a repository. Passed to knowledge_base."""
    repo_id: str
    repo_name: str  # owner/repo
    metadata: RepoMetadata
    files: list[ExtractedFile] = field(default_factory=list)
    commits: list[ExtractedCommit] = field(default_factory=list)
    branches: list[ExtractedBranch] = field(default_factory=list)
    issues: list[ExtractedIssue] = field(default_factory=list)
    pull_requests: list[ExtractedPR] = field(default_factory=list)


@dataclass
class IngestionProgress:
    stage: str       # cloning | extracting_files | extracting_commits | fetching_metadata | fetching_issues | fetching_prs | building_knowledge_base | complete | failed | paused
    current: int     # items processed
    total: int       # total items (-1 if unknown)
    message: str     # human-readable status
    # Only set when paused due to rate limit
    resets_at: datetime | None = None
    paused_reason: str = ""
