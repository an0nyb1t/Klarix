"""
Chat request/response schemas.

These are used by the API layer to validate input and format output.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChatRequest:
    message: str
    llm_config_override: dict | None = None  # Optional per-request LLM config


@dataclass
class MessageOut:
    id: str
    conversation_id: str
    role: str          # "user" | "assistant"
    content: str
    has_diff: bool
    created_at: datetime


@dataclass
class ConversationOut:
    id: str
    repository_id: str
    title: str
    created_at: datetime
    message_count: int = 0
