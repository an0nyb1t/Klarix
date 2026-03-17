"""
GitHub URL parser and validator.

Accepts multiple URL formats and extracts owner + repo name.
"""

import re
from dataclasses import dataclass


# Matches: github.com/owner/repo (with optional https://, .git suffix, /tree/branch, etc.)
_GITHUB_PATTERN = re.compile(
    r"(?:https?://)?github\.com/([^/]+)/([^/\s?#]+?)(?:\.git)?(?:/.*)?$",
    re.IGNORECASE,
)


@dataclass
class ParsedRepoURL:
    owner: str
    repo: str
    canonical_url: str  # https://github.com/owner/repo

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    def clone_url_with_token(self, token: str) -> str:
        return f"https://{token}@github.com/{self.owner}/{self.repo}.git"


class InvalidGitHubURL(ValueError):
    pass


def parse_github_url(url: str) -> ParsedRepoURL:
    """
    Parse a GitHub repository URL and return owner + repo.

    Accepts:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        https://github.com/owner/repo/tree/branch
        https://github.com/owner/repo/issues
        github.com/owner/repo

    Raises InvalidGitHubURL if the URL is not a valid GitHub repo URL.
    """
    url = url.strip()

    if not url:
        raise InvalidGitHubURL("URL cannot be empty.")

    match = _GITHUB_PATTERN.match(url)
    if not match:
        raise InvalidGitHubURL(
            f"'{url}' is not a valid GitHub repository URL. "
            "Expected format: https://github.com/owner/repo"
        )

    owner, repo = match.group(1), match.group(2)

    # Basic sanity checks
    if not owner or not repo:
        raise InvalidGitHubURL("Could not extract owner and repository name from URL.")

    if owner in (".", "..") or repo in (".", ".."):
        raise InvalidGitHubURL("Invalid owner or repository name.")

    # GitHub names can only contain alphanumeric, hyphens, underscores, and dots
    if not re.match(r"^[A-Za-z0-9_.\-]+$", owner):
        raise InvalidGitHubURL(f"Invalid GitHub username: '{owner}'")
    if not re.match(r"^[A-Za-z0-9_.\-]+$", repo):
        raise InvalidGitHubURL(f"Invalid repository name: '{repo}'")

    return ParsedRepoURL(
        owner=owner,
        repo=repo,
        canonical_url=f"https://github.com/{owner}/{repo}",
    )
