"""
Chat service — conversation management and RAG pipeline orchestration.

ChatService is the main entry point for the chat module. It:
  - Creates and manages conversations
  - Orchestrates the RAG pipeline for each message
  - Persists messages to SQLite
  - Streams LLM responses back to the caller
  - Compresses old messages into a rolling summary (V1.2)
"""

import asyncio
import logging
from collections.abc import AsyncGenerator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.prompts import (
    SUMMARIZATION_SYSTEM,
    assemble_messages,
    build_context_block,
    build_summarization_prompt,
    build_system_prompt,
    compute_context_budget,
)
from app.chat.rag import (
    contains_diff,
    enhance_question_for_diff,
    is_change_request,
    retrieve_context,
)
from app.llm.config import LLMConfig, default_config
from app.llm.service import LLMService
from models import Conversation, Message, Repository

logger = logging.getLogger(__name__)

# Summarization constants
KEEP_RECENT = 10       # Always send the last 10 unsummarized messages in full
SUMMARIZE_BATCH = 10   # Summarize this many messages per batch
                       # First trigger: total unsummarized > KEEP_RECENT + SUMMARIZE_BATCH

# Cheapest model per provider (used for summarization to save cost)
CHEAPEST_MODEL: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "claude_code": "haiku",
}

# Rough estimate of model context window (in tokens) used for budget calculation.
DEFAULT_MODEL_CONTEXT_TOKENS = 8192


class ChatStream:
    """Wraps the LLM streaming generator and exposes the assistant message ID after iteration."""

    def __init__(self):
        self.assistant_message_id: str | None = None
        self._generator: AsyncGenerator[str, None] | None = None

    def __aiter__(self):
        return self._generator.__aiter__()


class ChatService:
    def __init__(self, db: AsyncSession, llm_service: LLMService):
        self._db = db
        self._llm = llm_service

    # ── Conversation management ───────────────────────────────────────────────

    async def create_conversation(self, repo_id: str) -> Conversation:
        """Create a new conversation for a repository."""
        conv = Conversation(repository_id=repo_id, title="New conversation")
        self._db.add(conv)
        await self._db.flush()
        await self._db.refresh(conv)
        await self._db.commit()
        return conv

    async def get_conversation_history(self, conversation_id: str) -> list[Message]:
        """Get all messages in a conversation, ordered by creation time."""
        result = await self._db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return list(result.scalars().all())

    async def list_conversations(self, repo_id: str) -> list[Conversation]:
        """List all conversations for a repository, most recent first."""
        result = await self._db.execute(
            select(Conversation)
            .where(Conversation.repository_id == repo_id)
            .order_by(Conversation.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation and all its messages."""
        result = await self._db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv:
            await self._db.delete(conv)
            await self._db.commit()

    # ── RAG pipeline ─────────────────────────────────────────────────────────

    async def send_message(
        self,
        conversation_id: str,
        user_message: str,
        llm_config: LLMConfig | None = None,
    ) -> "ChatStream":
        """
        Full RAG pipeline. Returns a ChatStream that yields streaming response chunks.

        After streaming completes, saves both the user message and assistant response
        to the database, then fires background summarization if needed.

        Raises:
            ValueError — if conversation or its repository not found.
            LLMError — if the LLM call fails.
            RateLimitExceeded — if TPM limit is hit.
        """
        cfg = llm_config or default_config()

        # Load conversation and its repository
        conv = await self._load_conversation(conversation_id)
        repo = await self._load_repository(conv.repository_id)

        # Get repo metadata for the system prompt
        meta = repo.metadata_json or {}
        repo_description = meta.get("description", "")
        primary_language = meta.get("primary_language", "")

        # Build system prompt
        system_prompt = build_system_prompt(
            repo_name=repo.name,
            repo_description=repo_description,
            primary_language=primary_language,
        )

        # Optionally enhance question if it's a change request
        question = user_message
        change_request = is_change_request(user_message)
        if change_request:
            question = enhance_question_for_diff(user_message)

        # Retrieve relevant context — force code chunks for change requests
        chunks = await retrieve_context(
            conv.repository_id,
            question,
            force_code=change_request,
        )

        # Compute context budget and build context block
        context_budget = compute_context_budget(
            model_context_tokens=DEFAULT_MODEL_CONTEXT_TOKENS,
            response_tokens=cfg.max_tokens,
        )
        context_block = build_context_block(chunks, context_budget)

        # Get unsummarized messages (all messages after the summarization cutoff)
        recent_messages = await self._get_unsummarized_messages(
            conversation_id=conversation_id,
            summarized_count=conv.summarized_message_count or 0,
        )

        # Assemble full message list.
        # Pass `question` (may include diff format instructions) to the LLM.
        # `user_message` (original) is saved to DB so the user sees what they typed.
        messages = assemble_messages(
            system_prompt=system_prompt,
            context_block=context_block,
            conversation_summary=conv.summary,
            recent_messages=recent_messages,
            user_question=question,
        )

        # Stream LLM response, collecting the full text for persistence
        full_response: list[str] = []
        stream = ChatStream()

        async def _stream() -> AsyncGenerator[str, None]:
            async for chunk in self._llm.chat_completion(messages, cfg, stream=True):
                full_response.append(chunk)
                yield chunk

            # After streaming completes, persist both messages
            response_text = "".join(full_response)
            msg_id = await self._save_message_pair(
                conversation_id=conversation_id,
                user_content=user_message,
                assistant_content=response_text,
            )
            stream.assistant_message_id = msg_id

            # Update conversation title from first message if it's still default
            if conv.title == "New conversation":
                await self._auto_title_conversation(conv, user_message)

            # Fire background summarization (must not block streaming)
            asyncio.create_task(self._maybe_summarize(conversation_id, cfg))

        stream._generator = _stream()
        return stream

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _load_conversation(self, conversation_id: str) -> Conversation:
        result = await self._db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise ValueError(f"Conversation '{conversation_id}' not found.")
        return conv

    async def _load_repository(self, repo_id: str) -> Repository:
        result = await self._db.execute(
            select(Repository).where(Repository.id == repo_id)
        )
        repo = result.scalar_one_or_none()
        if repo is None:
            raise ValueError(f"Repository '{repo_id}' not found.")
        return repo

    async def _get_unsummarized_messages(
        self,
        conversation_id: str,
        summarized_count: int,
    ) -> list[Message]:
        """
        Fetch all messages after the summarization cutoff, ordered by time.

        No limit applied — between batches the count grows from KEEP_RECENT to
        KEEP_RECENT + SUMMARIZE_BATCH - 1, which is bounded and fits in the prompt.
        """
        result = await self._db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .offset(summarized_count)
        )
        return list(result.scalars().all())

    async def _save_message_pair(
        self,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
    ) -> str:
        """Persist the user message and assistant response to the DB. Returns assistant message ID."""
        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=user_content,
            has_diff=False,
        )
        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_content,
            has_diff=contains_diff(assistant_content),
        )
        self._db.add(user_msg)
        self._db.add(assistant_msg)
        await self._db.flush()
        await self._db.commit()
        return assistant_msg.id

    async def _auto_title_conversation(
        self,
        conv: Conversation,
        first_message: str,
    ) -> None:
        """Set a meaningful title from the first message (truncated)."""
        title = first_message.strip()[:80]
        if len(first_message.strip()) > 80:
            title += "..."
        conv.title = title
        await self._db.flush()
        await self._db.commit()

    # ── Summarization ─────────────────────────────────────────────────────────

    async def _maybe_summarize(self, conversation_id: str, ref_config: LLMConfig) -> None:
        """
        Background task: check if a summarization batch is ready and run it if so.

        Must open its own DB session — the request-scoped session is closed by the time
        this runs as an asyncio background task.
        """
        from database import AsyncSessionLocal  # avoid circular import at module level

        try:
            async with AsyncSessionLocal() as db:
                conv = await db.get(Conversation, conversation_id)
                if not conv:
                    return

                total = await db.scalar(
                    select(func.count(Message.id)).where(
                        Message.conversation_id == conversation_id
                    )
                )
                in_full = (total or 0) - (conv.summarized_message_count or 0)

                # Only summarize when a full batch is sitting outside the recent window
                if in_full >= KEEP_RECENT + SUMMARIZE_BATCH:
                    await self._run_summarization(conv, db, ref_config)
        except Exception:
            logger.exception("Summarization failed for conversation %s", conversation_id)

    async def _run_summarization(
        self,
        conversation: Conversation,
        db: AsyncSession,
        ref_config: LLMConfig,
    ) -> None:
        """Summarize one batch of messages and update the conversation record."""
        # Fetch the next SUMMARIZE_BATCH messages after the current cutoff
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
            .offset(conversation.summarized_message_count or 0)
            .limit(SUMMARIZE_BATCH)
        )
        batch = list(result.scalars().all())
        if not batch:
            return

        new_summary = await self._call_summarizer(
            existing_summary=conversation.summary,
            messages=batch,
            conversation=conversation,
            ref_config=ref_config,
        )

        conversation.summary = new_summary
        conversation.summarized_message_count = (conversation.summarized_message_count or 0) + len(batch)
        await db.commit()

    async def _call_summarizer(
        self,
        existing_summary: str | None,
        messages: list[Message],
        conversation: Conversation,
        ref_config: LLMConfig,
    ) -> str:
        """Call the LLM to produce an updated summary. Uses the cheapest available model."""
        from config import settings as app_settings  # import the in-memory singleton

        effective_provider = conversation.llm_provider or app_settings.llm_provider
        cheap_model = CHEAPEST_MODEL.get(effective_provider, ref_config.model)

        summarize_config = LLMConfig(
            provider=effective_provider,
            model=cheap_model,
            api_key=ref_config.api_key,
            base_url=ref_config.base_url,
            temperature=0.0,
            max_tokens=800,
            rate_limit_tpm=0,
        )

        prompt = build_summarization_prompt(existing_summary, messages)
        llm_messages = [
            {"role": "system", "content": SUMMARIZATION_SYSTEM},
            {"role": "user", "content": prompt},
        ]

        response = await self._llm.chat_completion_sync(llm_messages, summarize_config)
        return response
