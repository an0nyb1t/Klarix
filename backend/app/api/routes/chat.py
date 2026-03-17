"""
WebSocket chat route.

Route:
  WS /conversations/{conversation_id}/chat

Client sends:  { "message": "How does auth work?" }
Server streams:
  { "type": "chunk", "content": "The auth" }
  { "type": "chunk", "content": " middleware..." }
  { "type": "done", "message_id": "<uuid>" }

On error:
  { "type": "error", "message": "..." }

On rate limit:
  { "type": "rate_limited", "message": "...", "resets_at": "..." }
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.chat.service import ChatService
from app.llm.config import LLMConfig
from app.llm.exceptions import LLMError
from app.llm.service import LLMService
from config import settings as app_settings
from database import AsyncSessionLocal
from models import Conversation
from rate_limiter import RateLimitExceeded, RateLimitManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


@router.websocket("/conversations/{conversation_id}/chat")
async def chat_websocket(conversation_id: str, websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat.

    Each message from the client triggers a full RAG pipeline run,
    with response chunks streamed back as they arrive from the LLM.
    """
    await websocket.accept()

    async with AsyncSessionLocal() as db:
        rate_limiter = RateLimitManager(db)
        llm_svc = LLMService(rate_limiter=rate_limiter)
        chat_svc = ChatService(db=db, llm_service=llm_svc)

        try:
            while True:
                # Wait for a message from the client
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    logger.debug("WebSocket disconnected for conversation %s", conversation_id)
                    return

                # Parse the incoming message
                try:
                    data = json.loads(raw)
                    user_message = data.get("message", "").strip()
                    llm_config_override = data.get("llm_config_override")
                except (json.JSONDecodeError, AttributeError):
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Invalid message format. Expected JSON with a 'message' field.",
                    }))
                    continue

                if not user_message:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Message cannot be empty.",
                    }))
                    continue

                # Resolve effective provider/model: conversation override > global settings
                conv_result = await db.execute(
                    select(Conversation).where(Conversation.id == conversation_id)
                )
                conv = conv_result.scalar_one_or_none()

                effective_provider = (
                    (conv.llm_provider if conv else None) or app_settings.llm_provider
                )
                effective_model = (
                    (conv.llm_model if conv else None) or app_settings.llm_model
                )

                # Build LLM config — conversation override takes priority, then global
                llm_config = LLMConfig(
                    provider=effective_provider,
                    model=effective_model,
                    api_key=app_settings.llm_api_key,
                    base_url=app_settings.llm_base_url,
                    rate_limit_tpm=app_settings.llm_rate_limit_tpm,
                )

                # Allow client-side per-request overrides on top (rarely used)
                if llm_config_override:
                    try:
                        llm_config = LLMConfig(**llm_config_override)
                    except Exception:
                        pass  # Ignore invalid overrides, keep the resolved config

                # Run the RAG pipeline and stream chunks back
                try:
                    stream = await chat_svc.send_message(
                        conversation_id=conversation_id,
                        user_message=user_message,
                        llm_config=llm_config,
                    )

                    async for chunk in stream:
                        await websocket.send_text(json.dumps({
                            "type": "chunk",
                            "content": chunk,
                        }))

                    await websocket.send_text(json.dumps({
                        "type": "done",
                        "message_id": stream.assistant_message_id,
                    }))

                except ValueError as e:
                    # Conversation or repo not found
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": str(e),
                    }))

                except RateLimitExceeded as e:
                    resets_at_str = e.resets_at.isoformat() if e.resets_at else None
                    await websocket.send_text(json.dumps({
                        "type": "rate_limited",
                        "message": str(e),
                        "resets_at": resets_at_str,
                    }))

                except LLMError as e:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": str(e),
                    }))

                except Exception as e:
                    logger.exception("Unexpected error in chat WebSocket for %s", conversation_id)
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "An unexpected error occurred. Please try again.",
                    }))

        except WebSocketDisconnect:
            logger.debug("WebSocket disconnected for conversation %s", conversation_id)
