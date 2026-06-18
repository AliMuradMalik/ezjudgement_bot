"""Pydantic models used by the HTTP API."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# --- Chat ---------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[UUID] = Field(
        default=None,
        description="Continue an existing conversation. Omit to start a new one.",
    )


class ToolCall(BaseModel):
    tool_type: str
    queries: list[str] = Field(default_factory=list)
    result_count: Optional[int] = None


class Citation(BaseModel):
    file_id: str
    filename: Optional[str] = None
    judgment_url: Optional[str] = None
    headnote_url: Optional[str] = None
    url: Optional[str] = None    # web page URL (web_search citations)
    title: Optional[str] = None  # web page title (web_search citations)


class ChatResponse(BaseModel):
    conversation_id: UUID
    message_id: int
    answer: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


# --- Conversations ------------------------------------------------------------

class ConversationCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)


class ConversationSummary(BaseModel):
    id: UUID
    title: Optional[str]
    user_identifier: Optional[str]
    created_at: datetime
    updated_at: datetime
    message_count: int


class Message(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ConversationDetail(BaseModel):
    id: UUID
    title: Optional[str]
    user_identifier: Optional[str]
    created_at: datetime
    updated_at: datetime
    messages: list[Message]


# --- Health -------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str


class DbHealthResponse(BaseModel):
    status: str
    latency_ms: float
