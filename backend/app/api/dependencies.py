"""
FastAPI dependency injection.

Provides reusable dependencies for DB sessions and service instances.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import ChatService
from app.llm.service import LLMService
from database import get_db
from rate_limiter import RateLimitManager


async def get_llm_service(
    db: AsyncSession = Depends(get_db),
) -> LLMService:
    """LLMService wired with a RateLimitManager."""
    rate_limiter = RateLimitManager(db)
    return LLMService(rate_limiter=rate_limiter)


async def get_chat_service(
    db: AsyncSession = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service),
) -> ChatService:
    """ChatService wired with DB and LLMService."""
    return ChatService(db=db, llm_service=llm_service)
