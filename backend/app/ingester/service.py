"""
Ingestion orchestrator.

Coordinates git cloning, data extraction, GitHub API calls,
progress tracking, checkpointing, and rate limit handling.

All public functions are async. Heavy sync work (GitPython, PyGithub)
is dispatched via asyncio.to_thread().
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingester.git_service import (
    GitServiceError,
    RepoTooLargeError,
    clone_repository,
    extract_branches,
    extract_commits,
    extract_files,
    fetch_updates,
)
from app.ingester.github_service import GitHubService, GitHubServiceError
from app.ingester.schemas import ExtractedBranch, ExtractedData, IngestionProgress
from app.ingester.url_parser import InvalidGitHubURL, ParsedRepoURL, parse_github_url
from checkpoint import CheckpointManager
from config import settings
from models import Repository
from rate_limiter import RateLimitExceeded, RateLimitManager

logger = logging.getLogger(__name__)


# ── In-memory progress store ─────────────────────────────────────────────────
# The SSE endpoint reads from this. Keyed by repo_id.
_progress: dict[str, IngestionProgress] = {}


def get_progress(repo_id: str) -> IngestionProgress | None:
    return _progress.get(repo_id)


def clear_progress(repo_id: str) -> None:
    """Remove progress entry once the SSE client has received the terminal event."""
    _progress.pop(repo_id, None)


def _set_progress(repo_id: str, stage: str, current: int, total: int, message: str,
                  resets_at: datetime | None = None, paused_reason: str = "") -> None:
    _progress[repo_id] = IngestionProgress(
        stage=stage,
        current=current,
        total=total,
        message=message,
        resets_at=resets_at,
        paused_reason=paused_reason,
    )


def _clone_path(parsed: ParsedRepoURL) -> str:
    return os.path.join(settings.data_dir, "repos", f"{parsed.owner}_{parsed.repo}.git")


# ── DB helpers ───────────────────────────────────────────────────────────────

async def _get_or_create_repo(db: AsyncSession, parsed: ParsedRepoURL, url: str) -> Repository:
    result = await db.execute(select(Repository).where(Repository.url == parsed.canonical_url))
    repo = result.scalar_one_or_none()
    if repo is None:
        repo = Repository(
            url=parsed.canonical_url,
            name=parsed.full_name,
            status="pending",
        )
        db.add(repo)
        await db.flush()
        await db.refresh(repo)
    return repo


async def _set_repo_status(db: AsyncSession, repo: Repository, status: str) -> None:
    repo.status = status
    await db.flush()


# ── Main entry points ────────────────────────────────────────────────────────

async def ingest_repository(
    repo_url: str,
    db: AsyncSession,
    existing_repo_id: str | None = None,
) -> Repository:
    """
    Full ingestion pipeline:
    1. Parse URL
    2. Load existing repo (if existing_repo_id provided) or get/create
    3. Check for existing checkpoint → resume if found
    4. Clone repo
    5. Extract git data
    6. Check rate limit → fetch GitHub API data
    7. Pass data to knowledge_base
    8. Mark ready

    Pass existing_repo_id when the route has already created the repo record,
    to avoid a redundant SELECT/INSERT.
    """
    # 1. Parse & validate URL
    try:
        parsed = parse_github_url(repo_url)
    except InvalidGitHubURL as e:
        raise ValueError(str(e))

    # 2. Load existing repo or get/create
    if existing_repo_id:
        result = await db.execute(select(Repository).where(Repository.id == existing_repo_id))
        repo = result.scalar_one_or_none()
        if repo is None:
            raise ValueError(f"Repository '{existing_repo_id}' not found.")
    else:
        repo = await _get_or_create_repo(db, parsed, repo_url)
        await db.commit()

    # Check for existing checkpoint — resume instead of starting over
    cp_mgr = CheckpointManager(db)
    checkpoint = await cp_mgr.load(repo.id, "ingestion")
    if checkpoint:
        logger.info("Found existing checkpoint for %s — resuming.", parsed.full_name)
        return await _resume_ingestion(repo, parsed, checkpoint, cp_mgr, db)

    await _set_repo_status(db, repo, "ingesting")
    await db.commit()

    _set_progress(repo.id, "cloning", 0, -1, f"Cloning {parsed.full_name}...")

    try:
        return await _run_ingestion(repo, parsed, cp_mgr, db, checkpoint_state={})
    except Exception as e:
        await _set_repo_status(db, repo, "failed")
        await db.commit()
        _set_progress(repo.id, "failed", 0, 0, f"Ingestion failed: {e}")
        logger.exception("Ingestion failed for %s", parsed.full_name)
        raise


async def _run_ingestion(
    repo: Repository,
    parsed: ParsedRepoURL,
    cp_mgr: CheckpointManager,
    db: AsyncSession,
    checkpoint_state: dict,
) -> Repository:
    """Execute ingestion from a given state (empty dict = fresh start)."""
    clone_path = _clone_path(parsed)
    token = settings.github_token

    # ── Step 1: Clone ────────────────────────────────────────────────────────
    if not checkpoint_state.get("clone_complete"):
        _set_progress(repo.id, "cloning", 0, -1, f"Cloning {parsed.full_name}...")

        clone_url = parsed.clone_url_with_token(token) if token else parsed.clone_url
        try:
            await asyncio.to_thread(clone_repository, clone_url, clone_path)
        except GitServiceError as e:
            raise

        checkpoint_state["clone_complete"] = True

    # ── Step 2: Open repo ────────────────────────────────────────────────────
    import git as gitpython
    git_repo = await asyncio.to_thread(gitpython.Repo, clone_path)

    # ── Step 3: Extract branches ─────────────────────────────────────────────
    if not checkpoint_state.get("branches_extracted"):
        _set_progress(repo.id, "extracting_branches", 0, -1, "Extracting branches...")
        branches = await asyncio.to_thread(extract_branches, git_repo)
        checkpoint_state["branches_extracted"] = True
        checkpoint_state["branches"] = [{"name": b.name, "head": b.head_commit_hash} for b in branches]

    # ── Step 4: Extract commits ───────────────────────────────────────────────
    if not checkpoint_state.get("commits_extracted"):
        _set_progress(repo.id, "extracting_commits", 0, -1, "Counting commits...")

        def progress_cb(current, total):
            _set_progress(repo.id, "extracting_commits", current, total,
                          f"Extracting commits ({current}/{total})...")

        try:
            commits = await asyncio.to_thread(extract_commits, git_repo, progress_cb)
        except RepoTooLargeError as e:
            raise ValueError(str(e))

        checkpoint_state["commits_extracted"] = True
        checkpoint_state["total_commits"] = len(commits)
    else:
        # Commits were already extracted before pause — re-extract from clone
        _set_progress(repo.id, "extracting_commits", 0, -1, "Re-extracting commits...")
        commits = await asyncio.to_thread(extract_commits, git_repo)

    # ── Step 5: Extract files ─────────────────────────────────────────────────
    if not checkpoint_state.get("files_extracted"):
        # Determine default branch
        gh_default = checkpoint_state.get("metadata", {}).get("default_branch", "HEAD")

        def file_progress_cb(current, total):
            _set_progress(repo.id, "extracting_files", current, total,
                          f"Extracting files ({current}/{total})...")

        files = await asyncio.to_thread(extract_files, git_repo, gh_default, file_progress_cb)
        checkpoint_state["files_extracted"] = True
        checkpoint_state["total_files"] = len(files)
    else:
        # Files were already extracted before pause — re-extract from clone
        gh_default = checkpoint_state.get("metadata", {}).get("default_branch", "HEAD")
        _set_progress(repo.id, "extracting_files", 0, -1, "Re-extracting files...")
        files = await asyncio.to_thread(extract_files, git_repo, gh_default)

    # ── Step 6: GitHub API — metadata ─────────────────────────────────────────
    rate_mgr = RateLimitManager(db)
    gh_svc = GitHubService(token=token, rate_limiter=rate_mgr)

    if not checkpoint_state.get("metadata_fetched"):
        _set_progress(repo.id, "fetching_metadata", 0, -1, "Fetching repository metadata...")
        try:
            metadata, gh_repo_obj = await gh_svc.get_metadata(parsed.owner, parsed.repo)
        except RateLimitExceeded as e:
            return await _pause_ingestion(repo, cp_mgr, db, checkpoint_state, e,
                                          "fetching_metadata")
        except GitHubServiceError as e:
            raise ValueError(str(e))

        checkpoint_state["metadata_fetched"] = True
        checkpoint_state["metadata"] = {
            "description": metadata.description,
            "primary_language": metadata.primary_language,
            "stars": metadata.stars,
            "forks": metadata.forks,
            "topics": metadata.topics,
            "license_name": metadata.license_name,
            "default_branch": metadata.default_branch,
            "is_private": metadata.is_private,
        }

        # Update repo record with metadata
        repo.default_branch = metadata.default_branch
        repo.total_commits = checkpoint_state.get("total_commits", 0)
        repo.total_files = checkpoint_state.get("total_files", 0)
        repo.metadata_json = checkpoint_state["metadata"]
        await db.flush()
        await db.commit()
    else:
        # Metadata already fetched — reconstruct from checkpoint
        from app.ingester.schemas import RepoMetadata as _RM
        meta_dict = checkpoint_state.get("metadata", {})
        metadata = _RM(**meta_dict)
        gh_repo_obj = None  # Will need to re-fetch for issues/PRs if needed

    # ── Step 7: GitHub API — issues ───────────────────────────────────────────
    completed_issues = set(checkpoint_state.get("completed_issues", []))
    issues = []

    if not checkpoint_state.get("issues_fetched"):
        _set_progress(repo.id, "fetching_issues", len(completed_issues), -1,
                      "Fetching issues...")

        # Re-fetch gh_repo if needed
        if gh_repo_obj is None:
            try:
                _, gh_repo_obj = await gh_svc.get_metadata(parsed.owner, parsed.repo)
            except RateLimitExceeded as e:
                return await _pause_ingestion(repo, cp_mgr, db, checkpoint_state, e,
                                              "fetching_issues")

        def issue_progress_cb(current, total):
            _set_progress(repo.id, "fetching_issues", current, total,
                          f"Fetching issues ({current}/{total})...")

        try:
            issues = await gh_svc.get_issues(gh_repo_obj, completed_issues, issue_progress_cb)
            checkpoint_state["issues_fetched"] = True
            checkpoint_state["completed_issues"] = [i.number for i in issues]
            checkpoint_state["total_issues"] = len(issues)
        except RateLimitExceeded as e:
            # Save whatever we got
            checkpoint_state["completed_issues"] = [i.number for i in issues]
            return await _pause_ingestion(repo, cp_mgr, db, checkpoint_state, e,
                                          "fetching_issues")

    # ── Step 8: GitHub API — pull requests ────────────────────────────────────
    completed_prs = set(checkpoint_state.get("completed_prs", []))
    pull_requests = []

    if not checkpoint_state.get("prs_fetched"):
        _set_progress(repo.id, "fetching_prs", len(completed_prs), -1,
                      "Fetching pull requests...")

        if gh_repo_obj is None:
            try:
                _, gh_repo_obj = await gh_svc.get_metadata(parsed.owner, parsed.repo)
            except RateLimitExceeded as e:
                return await _pause_ingestion(repo, cp_mgr, db, checkpoint_state, e,
                                              "fetching_prs")

        def pr_progress_cb(current, total):
            _set_progress(repo.id, "fetching_prs", current, total,
                          f"Fetching PRs ({current}/{total})...")

        try:
            pull_requests = await gh_svc.get_pull_requests(gh_repo_obj, completed_prs, pr_progress_cb)
            checkpoint_state["prs_fetched"] = True
            checkpoint_state["completed_prs"] = [pr.number for pr in pull_requests]
            checkpoint_state["total_prs"] = len(pull_requests)
        except RateLimitExceeded as e:
            checkpoint_state["completed_prs"] = [pr.number for pr in pull_requests]
            return await _pause_ingestion(repo, cp_mgr, db, checkpoint_state, e,
                                          "fetching_prs")

    # ── Step 9: Build knowledge base ────────────────────────────────────────
    from app.ingester.schemas import RepoMetadata as RM
    branch_objs = [
        ExtractedBranch(name=b["name"], head_commit_hash=b["head"])
        for b in checkpoint_state.get("branches", [])
    ]
    meta_dict = checkpoint_state.get("metadata", {})
    meta_obj = RM(**meta_dict) if meta_dict else metadata

    extracted_data = ExtractedData(
        repo_id=repo.id,
        repo_name=repo.name,
        metadata=meta_obj,
        files=files,
        commits=commits,
        branches=branch_objs,
        issues=issues,
        pull_requests=pull_requests,
    )

    from app.knowledge_base.service import build_knowledge_base
    _set_progress(repo.id, "building_knowledge_base", 0, -1, "Building knowledge base...")
    await build_knowledge_base(repo.id, extracted_data, db)
    logger.info(
        "Ingestion + KB complete for %s — %d files, %d commits, %d issues, %d PRs.",
        repo.name, len(files), len(commits), len(issues), len(pull_requests),
    )

    # ── Step 10: Finalize ─────────────────────────────────────────────────────
    repo.status = "ready"
    repo.last_synced_at = datetime.now(timezone.utc)
    await db.flush()
    await db.commit()

    await cp_mgr.clear(repo.id, "ingestion")
    await db.commit()

    _set_progress(repo.id, "complete", 1, 1, f"Repository '{repo.name}' is ready.")

    return repo


async def _pause_ingestion(
    repo: Repository,
    cp_mgr: CheckpointManager,
    db: AsyncSession,
    state: dict,
    exc: RateLimitExceeded,
    stage: str,
) -> Repository:
    """Save checkpoint and mark repo as paused when rate limit is hit."""
    logger.warning("Rate limit hit at stage '%s' for %s. Pausing.", stage, repo.name)

    current = state.get("progress_current", 0)
    total = state.get("progress_total", -1)

    await cp_mgr.save(
        repo_id=repo.id,
        operation="ingestion",
        stage=stage,
        progress_current=current,
        progress_total=total,
        state=state,
        paused_reason=str(exc.service) + "_rate_limit",
        resets_at=exc.resets_at,
    )

    repo.status = "paused"
    await db.flush()
    await db.commit()

    _set_progress(
        repo.id, "paused", current, total,
        f"Paused: {exc}",
        resets_at=exc.resets_at,
        paused_reason=f"{exc.service}_rate_limit",
    )

    return repo


async def _resume_ingestion(
    repo: Repository,
    parsed: ParsedRepoURL,
    checkpoint,
    cp_mgr: CheckpointManager,
    db: AsyncSession,
) -> Repository:
    """Resume ingestion from a saved checkpoint."""
    logger.info("Resuming ingestion for %s from stage '%s'.", parsed.full_name, checkpoint.stage)

    await cp_mgr.mark_resumed(checkpoint.id)
    await _set_repo_status(db, repo, "ingesting")
    await db.commit()

    _set_progress(repo.id, checkpoint.stage, checkpoint.progress_current,
                  checkpoint.progress_total,
                  f"Resuming from '{checkpoint.stage}'...")

    state = dict(checkpoint.state)
    try:
        return await _run_ingestion(repo, parsed, cp_mgr, db, checkpoint_state=state)
    except Exception as e:
        await _set_repo_status(db, repo, "failed")
        await db.commit()
        _set_progress(repo.id, "failed", 0, 0, f"Resume failed: {e}")
        raise


# ── Re-sync ───────────────────────────────────────────────────────────────────

async def resync_repository(repo_id: str, db: AsyncSession) -> Repository:
    """
    Fetch new commits and updated issues/PRs for an already-ingested repo.
    Only processes delta since last sync.
    """
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise ValueError(f"Repository '{repo_id}' not found.")

    parsed = parse_github_url(repo.url)
    clone_path = _clone_path(parsed)

    await _set_repo_status(db, repo, "syncing")
    await db.commit()

    _set_progress(repo.id, "syncing", 0, -1, f"Syncing {repo.name}...")

    cp_mgr = CheckpointManager(db)
    token = settings.github_token

    try:
        # git fetch
        _set_progress(repo.id, "fetching_updates", 0, -1, "Fetching git updates...")
        await asyncio.to_thread(fetch_updates, clone_path)

        # Re-run ingestion with fresh state (incremental handled by git fetch)
        # For simplicity in V1, re-ingest fully after fetch
        # TODO: Optimize to only process delta commits
        import git as gitpython
        git_repo = await asyncio.to_thread(gitpython.Repo, clone_path)

        branches = await asyncio.to_thread(extract_branches, git_repo)
        commits = await asyncio.to_thread(extract_commits, git_repo)

        rate_mgr = RateLimitManager(db)
        gh_svc = GitHubService(token=token, rate_limiter=rate_mgr)

        try:
            metadata, gh_repo_obj = await gh_svc.get_metadata(parsed.owner, parsed.repo)
        except RateLimitExceeded as e:
            state = {"resync": True}
            return await _pause_resync(repo, cp_mgr, db, state, e)

        try:
            issues = await gh_svc.get_issues(gh_repo_obj)
            pull_requests = await gh_svc.get_pull_requests(gh_repo_obj)
        except RateLimitExceeded as e:
            state = {"resync": True, "metadata_fetched": True}
            return await _pause_resync(repo, cp_mgr, db, state, e)

        # Extract files so newly added files get embedded
        def file_progress_cb(current, total):
            _set_progress(repo.id, "extracting_files", current, total,
                          f"Re-indexing files ({current}/{total})...")

        files = await asyncio.to_thread(
            extract_files, git_repo, metadata.default_branch, file_progress_cb
        )

        # Rebuild knowledge base with updated data
        extracted_data = ExtractedData(
            repo_id=repo.id,
            repo_name=repo.name,
            metadata=metadata,
            files=files,
            commits=commits,
            branches=branches,
            issues=issues,
            pull_requests=pull_requests,
        )
        _set_progress(repo.id, "building_knowledge_base", 0, -1, "Updating knowledge base...")
        from app.knowledge_base.service import update_knowledge_base
        await update_knowledge_base(repo.id, extracted_data, db)

        repo.status = "ready"
        repo.last_synced_at = datetime.now(timezone.utc)
        repo.total_commits = len(commits)
        repo.total_files = len(files)
        await db.flush()
        await db.commit()

        await cp_mgr.clear(repo.id, "resync")
        await db.commit()

        _set_progress(repo.id, "complete", 1, 1, f"Sync complete for '{repo.name}'.")
        return repo

    except Exception as e:
        await _set_repo_status(db, repo, "failed")
        await db.commit()
        _set_progress(repo.id, "failed", 0, 0, f"Sync failed: {e}")
        raise


async def _pause_resync(repo, cp_mgr, db, state, exc):
    await cp_mgr.save(
        repo_id=repo.id, operation="resync", stage="paused",
        progress_current=0, progress_total=-1,
        state=state, paused_reason=f"{exc.service}_rate_limit",
        resets_at=exc.resets_at,
    )
    repo.status = "paused"
    await db.flush()
    await db.commit()
    _set_progress(repo.id, "paused", 0, -1, f"Sync paused: {exc}",
                  resets_at=exc.resets_at, paused_reason=f"{exc.service}_rate_limit")
    return repo
