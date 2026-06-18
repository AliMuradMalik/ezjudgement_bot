"""CRUD endpoints for chat conversations. Scoped per X-User-ID."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from starlette.concurrency import run_in_threadpool

from app.deps import require_user_id
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    Message,
)
from database import db

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _owned_or_404(conversation_id: str, user_id: str) -> dict:
    conv = await run_in_threadpool(db.get_conversation, conversation_id)
    if conv is None or conv["user_identifier"] != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conv


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    user_id: str = Depends(require_user_id),
) -> ConversationSummary:
    conv_id = await run_in_threadpool(db.create_conversation, user_id, body.title)
    row = await run_in_threadpool(db.get_conversation, conv_id)
    assert row is not None
    return ConversationSummary(message_count=0, **row)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    user_id: str = Depends(require_user_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationSummary]:
    rows = await run_in_threadpool(db.list_conversations, user_id, limit, offset)
    return [ConversationSummary(**r) for r in rows]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    user_id: str = Depends(require_user_id),
) -> ConversationDetail:
    conv = await _owned_or_404(str(conversation_id), user_id)
    messages = await run_in_threadpool(db.load_conversation_messages, str(conversation_id))
    return ConversationDetail(**conv, messages=[Message(**m) for m in messages])


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    user_id: str = Depends(require_user_id),
) -> Response:
    await _owned_or_404(str(conversation_id), user_id)
    await run_in_threadpool(db.delete_conversation, str(conversation_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
