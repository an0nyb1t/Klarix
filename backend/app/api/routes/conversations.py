"""
Conversation routes.

Routes:
  POST   /repos/{repo_id}/conversations                Create a new conversation
  GET    /repos/{repo_id}/conversations                List conversations for a repo
  GET    /conversations/{conversation_id}/messages     Get message history
  PATCH  /conversations/{conversation_id}              Update model override (V1.2)
  DELETE /conversations/{conversation_id}              Delete a conversation
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.schemas import ConversationOut, ConversationUpdateRequest, MessageOut, OkResponse
from app.chat.service import ChatService
from app.api.dependencies import get_chat_service
from database import get_db
from models import Conversation, Message, Repository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Conversations"])


def _conv_out(conv: Conversation, message_count: int = 0) -> ConversationOut:
    """Build a ConversationOut from an ORM Conversation object."""
    return ConversationOut(
        id=conv.id,
        repository_id=conv.repository_id,
        title=conv.title,
        created_at=conv.created_at,
        message_count=message_count,
        llm_provider=conv.llm_provider,
        llm_model=conv.llm_model,
        has_summary=conv.has_summary,
    )


@router.post(
    "/repos/{repo_id}/conversations",
    response_model=ConversationOut,
    status_code=201,
)
async def create_conversation(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    chat_svc: ChatService = Depends(get_chat_service),
):
    """Create a new conversation for a repository."""
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_id}' not found.")
    if repo.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Repository is not ready (status: {repo.status}). Wait for ingestion to complete.",
        )

    conv = await chat_svc.create_conversation(repo_id)
    return _conv_out(conv, message_count=0)


@router.get(
    "/repos/{repo_id}/conversations",
    response_model=list[ConversationOut],
)
async def list_conversations(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    chat_svc: ChatService = Depends(get_chat_service),
):
    """List all conversations for a repository, most recent first."""
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_id}' not found.")

    convs = await chat_svc.list_conversations(repo_id)

    # Load message counts in one query
    count_result = await db.execute(
        select(Message.conversation_id, func.count(Message.id).label("cnt"))
        .where(Message.conversation_id.in_([c.id for c in convs]))
        .group_by(Message.conversation_id)
    )
    counts = {row.conversation_id: row.cnt for row in count_result}

    return [_conv_out(c, message_count=counts.get(c.id, 0)) for c in convs]


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
)
async def get_messages(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    chat_svc: ChatService = Depends(get_chat_service),
):
    """Get all messages in a conversation, ordered by creation time."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=404,
            detail=f"Conversation '{conversation_id}' not found.",
        )

    messages = await chat_svc.get_conversation_history(conversation_id)
    return [
        MessageOut(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            has_diff=m.has_diff,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Update the model override for a conversation (V1.2).

    Both llm_provider and llm_model must be set together, or both null to clear the override.
    """
    if (body.llm_provider is None) != (body.llm_model is None):
        raise HTTPException(
            status_code=422,
            detail="llm_provider and llm_model must both be set or both be null.",
        )

    conv = await db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")

    conv.llm_provider = body.llm_provider
    conv.llm_model = body.llm_model
    await db.commit()
    await db.refresh(conv)

    # Count messages for the response
    count_result = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )
    message_count = count_result.scalar() or 0

    return _conv_out(conv, message_count=message_count)


@router.delete("/conversations/{conversation_id}", response_model=OkResponse)
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    chat_svc: ChatService = Depends(get_chat_service),
):
    """Delete a conversation and all its messages."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=404,
            detail=f"Conversation '{conversation_id}' not found.",
        )

    await chat_svc.delete_conversation(conversation_id)
    return OkResponse()
