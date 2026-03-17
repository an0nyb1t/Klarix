"""
GitHub API service — fetches issues, PRs, and repo metadata via PyGithub.

PyGithub is synchronous. All public methods here are async and wrap
PyGithub calls in asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from github import Github, GithubException, UnknownObjectException
from github.Repository import Repository as GithubRepo

from app.ingester.schemas import (
    ExtractedIssue,
    ExtractedPR,
    IssueComment,
    PRReviewComment,
    RepoMetadata,
)
from rate_limiter import RateLimitExceeded, RateLimitManager

logger = logging.getLogger(__name__)

BATCH_SIZE = 100  # Items per page / batch before rate limit check


class GitHubServiceError(Exception):
    pass


def _make_client(token: str) -> Github:
    return Github(token, per_page=BATCH_SIZE) if token else Github(per_page=BATCH_SIZE)


def _get_github_repo(client: Github, owner: str, repo: str) -> GithubRepo:
    """Synchronous — must be called inside asyncio.to_thread()."""
    try:
        return client.get_repo(f"{owner}/{repo}")
    except UnknownObjectException:
        raise GitHubServiceError(
            f"Repository '{owner}/{repo}' not found. "
            "If it's private, provide a GitHub token with repo access."
        )
    except GithubException as e:
        if e.status == 401:
            raise GitHubServiceError("Invalid GitHub token.")
        raise GitHubServiceError(f"GitHub API error: {e.data.get('message', str(e))}")


def _extract_metadata(gh_repo: GithubRepo) -> RepoMetadata:
    """Synchronous — must be called inside asyncio.to_thread()."""
    try:
        license_name = gh_repo.license.name if gh_repo.license else ""
    except Exception:
        license_name = ""

    return RepoMetadata(
        description=gh_repo.description or "",
        primary_language=gh_repo.language or "",
        stars=gh_repo.stargazers_count,
        forks=gh_repo.forks_count,
        topics=gh_repo.topics or [],
        license_name=license_name,
        default_branch=gh_repo.default_branch or "main",
        is_private=gh_repo.private,
    )


def _extract_issue(issue_obj: Any) -> ExtractedIssue:
    """Synchronous — must be called inside asyncio.to_thread()."""
    comments = []
    try:
        for c in issue_obj.get_comments():
            comments.append(IssueComment(
                author=c.user.login if c.user else "unknown",
                body=c.body or "",
                created_at=c.created_at.replace(tzinfo=timezone.utc) if c.created_at else datetime.now(timezone.utc),
            ))
    except Exception:
        pass

    closed_at = None
    if issue_obj.closed_at:
        closed_at = issue_obj.closed_at.replace(tzinfo=timezone.utc)

    return ExtractedIssue(
        number=issue_obj.number,
        title=issue_obj.title or "",
        body=issue_obj.body or "",
        labels=[lbl.name for lbl in issue_obj.labels],
        state=issue_obj.state,
        author=issue_obj.user.login if issue_obj.user else "unknown",
        comments=comments,
        created_at=issue_obj.created_at.replace(tzinfo=timezone.utc),
        closed_at=closed_at,
    )


def _extract_pr(pr_obj: Any) -> ExtractedPR:
    """Synchronous — must be called inside asyncio.to_thread()."""
    review_comments = []
    try:
        for rc in pr_obj.get_review_comments():
            review_comments.append(PRReviewComment(
                author=rc.user.login if rc.user else "unknown",
                body=rc.body or "",
                path=rc.path,
            ))
    except Exception:
        pass

    merged_at = None
    if pr_obj.merged_at:
        merged_at = pr_obj.merged_at.replace(tzinfo=timezone.utc)

    state = "merged" if pr_obj.merged else pr_obj.state

    return ExtractedPR(
        number=pr_obj.number,
        title=pr_obj.title or "",
        body=pr_obj.body or "",
        state=state,
        author=pr_obj.user.login if pr_obj.user else "unknown",
        is_merged=pr_obj.merged,
        merged_at=merged_at,
        review_comments=review_comments,
        created_at=pr_obj.created_at.replace(tzinfo=timezone.utc),
    )


class GitHubService:
    def __init__(self, token: str, rate_limiter: RateLimitManager):
        self._token = token
        self._rate_limiter = rate_limiter
        self._client = _make_client(token)

    async def get_metadata(self, owner: str, repo: str) -> tuple[RepoMetadata, Any]:
        """
        Fetch repository metadata. Returns (metadata, gh_repo_object).
        The gh_repo object is reused by other methods to avoid extra API calls.
        """
        await self._check_rate_limit()
        gh_repo, metadata = await asyncio.to_thread(
            self._fetch_metadata_sync, owner, repo
        )
        return metadata, gh_repo

    def _fetch_metadata_sync(self, owner: str, repo: str):
        gh_repo = _get_github_repo(self._client, owner, repo)
        metadata = _extract_metadata(gh_repo)
        return gh_repo, metadata

    async def get_issues(
        self,
        gh_repo: Any,
        completed_numbers: set[int] | None = None,
        progress_callback=None,
    ) -> list[ExtractedIssue]:
        """
        Fetch all issues (excluding PRs). Checks rate limit every BATCH_SIZE items.
        Skips issue numbers in completed_numbers (for resume after pause).
        """
        completed_numbers = completed_numbers or set()
        issues = []
        count = 0

        # Get total count without loading all into memory
        paginated = gh_repo.get_issues(state="all")
        total = await asyncio.to_thread(lambda: paginated.totalCount)

        # Iterate lazily — PyGithub paginates automatically
        page_num = 0
        while True:
            page = await asyncio.to_thread(lambda p=page_num: paginated.get_page(p))
            if not page:
                break

            for issue_obj in page:
                # Issues API returns PRs too — filter them out
                if issue_obj.pull_request:
                    continue

                if issue_obj.number in completed_numbers:
                    count += 1
                    continue

                try:
                    extracted = await asyncio.to_thread(_extract_issue, issue_obj)
                    issues.append(extracted)
                    count += 1
                    if progress_callback:
                        progress_callback(count, total)
                except Exception as e:
                    logger.warning("Failed to extract issue #%d: %s", issue_obj.number, e)

            page_num += 1
            # Rate limit check after each page
            await self._check_rate_limit()

        return issues

    async def get_pull_requests(
        self,
        gh_repo: Any,
        completed_numbers: set[int] | None = None,
        progress_callback=None,
    ) -> list[ExtractedPR]:
        """
        Fetch all pull requests. Checks rate limit every BATCH_SIZE items.
        Skips PR numbers in completed_numbers (for resume after pause).
        """
        completed_numbers = completed_numbers or set()
        prs = []
        count = 0

        # Get total count without loading all into memory
        paginated = gh_repo.get_pulls(state="all")
        total = await asyncio.to_thread(lambda: paginated.totalCount)

        # Iterate lazily — PyGithub paginates automatically
        page_num = 0
        while True:
            page = await asyncio.to_thread(lambda p=page_num: paginated.get_page(p))
            if not page:
                break

            for pr_obj in page:
                if pr_obj.number in completed_numbers:
                    count += 1
                    continue

                try:
                    extracted = await asyncio.to_thread(_extract_pr, pr_obj)
                    prs.append(extracted)
                    count += 1
                    if progress_callback:
                        progress_callback(count, total)
                except Exception as e:
                    logger.warning("Failed to extract PR #%d: %s", pr_obj.number, e)

            page_num += 1
            # Rate limit check after each page
            await self._check_rate_limit()

        return prs

    async def _check_rate_limit(self) -> None:
        """Check GitHub rate limit. Raises RateLimitExceeded if at 95%."""
        await asyncio.to_thread(
            self._rate_limiter_check_sync
        )

    def _rate_limiter_check_sync(self) -> None:
        """Synchronous rate limit check — wraps the async manager's sync internals."""
        rate = self._client.get_rate_limit().rate
        limit_max = rate.limit
        limit_remaining = rate.remaining
        limit_used = limit_max - limit_remaining
        usage_percent = limit_used / limit_max if limit_max > 0 else 0.0

        if usage_percent >= 0.95:
            from rate_limiter import RateLimitExceeded, RateLimitInfo
            import rate_limiter as rl_module
            resets_at = rate.reset
            info = RateLimitInfo(
                service="github",
                limit_max=limit_max,
                limit_remaining=limit_remaining,
                limit_used=limit_used,
                usage_percent=usage_percent,
                resets_at=resets_at,
                is_paused=True,
            )
            rl_module._cache["github"] = info
            raise RateLimitExceeded("github", usage_percent, resets_at)
