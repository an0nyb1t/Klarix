"""
RAG pipeline — the core of the chat engine.

Takes a user question and orchestrates:
  1. Query analysis (what content types to search)
  2. Retrieval from knowledge base
  3. Prompt assembly
  4. LLM call (streaming)
"""

import logging
import re

from app.knowledge_base.schemas import RetrievedChunk

logger = logging.getLogger(__name__)

# ── Query Analysis ────────────────────────────────────────────────────────────

# Keywords that bias toward specific content types
_CODE_KEYWORDS = re.compile(
    r"\b(function|class|method|file|module|import|def |variable|error|exception|"
    r"implement|code|syntax|line|return|parameter|argument|type)\b",
    re.IGNORECASE,
)
_COMMIT_KEYWORDS = re.compile(
    r"\b(commit|change[sd]?|when|who changed|author|history|version|update[sd]?|"
    r"add(ed)?|remov(ed)?|fix(ed)?|introduc(ed)?|refactor)\b",
    re.IGNORECASE,
)
_ISSUE_KEYWORDS = re.compile(
    r"\b(issue|bug|feature request|ticket|report(ed)?|label|open|close[sd]?)\b",
    re.IGNORECASE,
)
_PR_KEYWORDS = re.compile(
    r"\b(pull request|PR|merge[sd]?|review(ed)?|branch|diff)\b",
    re.IGNORECASE,
)


def analyze_query(question: str) -> list[str] | None:
    """
    Determine which content types to search based on keywords in the question.

    Returns a list of content type strings, or None to search all types.
    """
    types: set[str] = set()

    if _CODE_KEYWORDS.search(question):
        types.add("code")
    if _COMMIT_KEYWORDS.search(question):
        types.add("commit")
    if _ISSUE_KEYWORDS.search(question):
        types.add("issue")
    if _PR_KEYWORDS.search(question):
        types.add("pull_request")

    # Always include repo_overview — it's small and context-setting
    types.add("repo_overview")

    # If nothing specific was detected, search everything
    if types == {"repo_overview"}:
        return None

    return list(types)


# ── Diff Detection ────────────────────────────────────────────────────────────

_DIFF_PATTERN = re.compile(r"```diff\s*\n.*?```", re.DOTALL)

# Keywords indicating the user wants code changes
_CHANGE_REQUEST_KEYWORDS = re.compile(
    r"\b(add|remove|fix|update|change|modify|refactor|rewrite|implement|create|delete|"
    r"rename|move|extract|replace|insert)\b",
    re.IGNORECASE,
)


def contains_diff(text: str) -> bool:
    """Return True if the response contains a unified diff block."""
    return bool(_DIFF_PATTERN.search(text))


def is_change_request(question: str) -> bool:
    """Return True if the question is asking for code modifications."""
    return bool(_CHANGE_REQUEST_KEYWORDS.search(question))


def enhance_question_for_diff(question: str) -> str:
    """
    Append diff format instructions when the user is requesting code changes.
    This nudges the LLM to produce a clean unified diff.
    """
    return (
        f"{question}\n\n"
        "Please provide the changes as a unified diff patch "
        "(```diff ... ```) that can be applied with `git apply`."
    )


# ── Retrieve ──────────────────────────────────────────────────────────────────

async def retrieve_context(
    repo_id: str,
    question: str,
    n_results: int = 15,
    force_code: bool = False,
) -> list[RetrievedChunk]:
    """
    Retrieve relevant chunks from the knowledge base for this question.
    Uses keyword analysis to filter by content type when possible.

    force_code: when True, always include "code" in the content types.
    Use this for change requests so the LLM has source context to produce diffs.
    """
    from app.knowledge_base.retriever import retrieve

    content_types = analyze_query(question)

    # Change requests need source code to produce meaningful diffs.
    # If keyword routing didn't include code, add it now.
    if force_code and content_types is not None and "code" not in content_types:
        content_types.append("code")

    logger.debug(
        "RAG retrieve: repo=%s, types=%s, n=%d, force_code=%s",
        repo_id, content_types, n_results, force_code,
    )

    chunks = await retrieve(
        repo_id=repo_id,
        query=question,
        n_results=n_results,
        content_types=content_types,
    )

    logger.debug("RAG retrieved %d chunks for repo %s", len(chunks), repo_id)
    return chunks
