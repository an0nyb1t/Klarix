"""
Prompt assembly for the RAG pipeline.

Builds the system prompt, formats retrieved context chunks,
and manages the context window budget.
"""

from models import Message
from app.knowledge_base.schemas import RetrievedChunk

# Token budget constants (rough: 4 chars ≈ 1 token)
CHARS_PER_TOKEN = 4
SYSTEM_PROMPT_BUDGET = 1000    # tokens reserved for system prompt
SUMMARY_BUDGET = 600           # tokens reserved for the rolling conversation summary
# History budget is dynamic: KEEP_RECENT * avg_message_tokens (estimated at runtime)
# response budget comes from LLMConfig.max_tokens

SUMMARIZATION_SYSTEM = """You are a technical conversation summarizer.
You will be given a chat history between a developer and an AI assistant about a GitHub repository.
Your job is to write a concise summary that captures all important technical context."""


SYSTEM_PROMPT_TEMPLATE = """\
You are GitChat, an AI assistant that helps users understand GitHub repositories.
You have access to the repository's source code, commit history, issues, and pull requests.

Repository: {repo_name}
Description: {repo_description}
Primary language: {language}

Rules:
- Answer based on the provided context. If the context doesn't contain enough information, say so.
- When referencing code, always include the file path and line numbers.
- When asked to modify code, generate a unified diff patch in this format:
  ```diff
  --- a/path/to/file
  +++ b/path/to/file
  @@ -line,count +line,count @@
   context line
  -removed line
  +added line
  ```
  The user can apply this with `git apply patch.diff`.
- Be concise but thorough. Your audience is software developers and security researchers.\
"""


def build_system_prompt(
    repo_name: str,
    repo_description: str,
    primary_language: str,
) -> str:
    """Build the system prompt for this repository."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        repo_name=repo_name,
        repo_description=repo_description or "No description",
        language=primary_language or "unknown",
    )


def _format_chunk(chunk: RetrievedChunk) -> str:
    """Format a single retrieved chunk for the context block."""
    meta = chunk.metadata
    chunk_type = meta.get("type", "unknown")

    if chunk_type == "code":
        file_path = meta.get("file_path", "unknown")
        line_start = meta.get("line_start", "?")
        line_end = meta.get("line_end", "?")
        return f"[Code] {file_path} (lines {line_start}-{line_end}):\n{chunk.content}"

    elif chunk_type == "commit":
        short_hash = meta.get("short_hash", meta.get("commit_hash", "?")[:7])
        author = meta.get("author", "unknown")
        date = meta.get("date", "unknown")
        return f"[Commit] {short_hash} by {author} on {date}:\n{chunk.content}"

    elif chunk_type == "issue":
        number = meta.get("number", "?")
        title = meta.get("title", "")
        state = meta.get("state", "")
        return f"[Issue] #{number} ({state}): {title}\n{chunk.content}"

    elif chunk_type == "pull_request":
        number = meta.get("number", "?")
        title = meta.get("title", "")
        state = meta.get("state", "")
        return f"[PR] #{number} ({state}): {title}\n{chunk.content}"

    elif chunk_type == "media_ref":
        file_path = meta.get("file_path", "unknown")
        return f"[Media] {file_path}:\n{chunk.content}"

    elif chunk_type == "repo_overview":
        return f"[Repository Overview]:\n{chunk.content}"

    else:
        return f"[{chunk_type}]:\n{chunk.content}"


def build_context_block(
    chunks: list[RetrievedChunk],
    max_context_tokens: int,
) -> str:
    """
    Build the context block from retrieved chunks, respecting the token budget.

    Chunks are already sorted by relevance (highest first). We fill up to
    max_context_tokens, skipping any chunk that would exceed the budget.
    """
    if not chunks:
        return ""

    parts = ["--- Relevant Context ---\n"]
    tokens_used = 0
    included = 0

    for chunk in chunks:
        formatted = _format_chunk(chunk)
        chunk_tokens = len(formatted) // CHARS_PER_TOKEN

        if tokens_used + chunk_tokens > max_context_tokens:
            continue  # Skip — doesn't fit

        parts.append(formatted)
        parts.append("")  # Blank line between chunks
        tokens_used += chunk_tokens
        included += 1

    if included == 0:
        return ""

    parts.append("--- End Context ---")
    return "\n".join(parts)


def assemble_messages(
    system_prompt: str,
    context_block: str,
    conversation_summary: str | None,
    recent_messages: list[Message],
    user_question: str,
) -> list[dict]:
    """
    Assemble the full message list for the LLM.

    Structure:
    1. System message (system prompt + context)
    2. Rolling summary injected as a user/assistant exchange (if present)
    3. Recent messages in full (all unsummarized messages)
    4. Current user message
    """
    if context_block:
        system_content = f"{system_prompt}\n\n{context_block}"
    else:
        system_content = system_prompt

    messages: list[dict] = [{"role": "system", "content": system_content}]

    if conversation_summary:
        messages.append({
            "role": "user",
            "content": f"--- Earlier conversation (summarized) ---\n{conversation_summary}\n--- End summary ---",
        })
        messages.append({
            "role": "assistant",
            "content": "Understood. I have context from our earlier discussion.",
        })

    for msg in recent_messages:
        messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": user_question})
    return messages


def build_summarization_prompt(existing_summary: str | None, messages: list[Message]) -> str:
    """Build the prompt for the summarization LLM call."""
    parts = []
    if existing_summary:
        parts.append(f"Existing summary (from earlier in the conversation):\n{existing_summary}\n")

    parts.append("New messages to incorporate into the summary:")
    for msg in messages:
        parts.append(f"{msg.role.upper()}: {msg.content}")

    parts.append(
        "\nWrite a single updated summary that incorporates both the existing summary (if any) "
        "and the new messages.\n"
        "The summary must capture:\n"
        "- What technical questions were asked and what answers were given\n"
        "- Any code changes, diffs, or implementations discussed\n"
        "- Bugs or issues identified and their resolutions\n"
        "- File names, function names, or module names that came up\n"
        "- Any decisions or conclusions reached\n\n"
        "Keep the summary under 400 words. Write in plain prose, not bullet points."
    )

    return "\n\n".join(parts)


def compute_context_budget(model_context_tokens: int, response_tokens: int) -> int:
    """
    Compute how many tokens are available for retrieved context chunks.

    Budget = total model context - system prompt - summary reserve - response reserve
    """
    available = model_context_tokens - SYSTEM_PROMPT_BUDGET - SUMMARY_BUDGET - response_tokens
    return max(0, available)
