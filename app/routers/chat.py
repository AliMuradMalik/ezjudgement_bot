"""Chat endpoints — both regular JSON and streaming SSE."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from app.deps import get_rag_service, require_user_id
from app.rag import RagService
from app.schemas import ChatRequest, ChatResponse, Citation, ToolCall
from database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


async def _resolve_conversation(req: ChatRequest, user_id: str) -> tuple[str, bool]:
    """Return (conversation_id, is_new). Validates ownership when continuing."""
    if req.conversation_id is None:
        conversation_id = await run_in_threadpool(db.create_conversation, user_id, None)
        return conversation_id, True

    existing = await run_in_threadpool(db.get_conversation, str(req.conversation_id))
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    if existing["user_identifier"] != user_id:
        # Mask existence of other users' conversations.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return str(req.conversation_id), False


async def _persist_turn(conversation_id: str,
                        is_new: bool,
                        user_message: str,
                        result) -> int:
    await run_in_threadpool(db.save_message, conversation_id, "user", user_message)
    if is_new:
        await run_in_threadpool(db.set_conversation_title, conversation_id, user_message[:80])

    assistant_id = await run_in_threadpool(
        db.save_message,
        conversation_id, "assistant", result.answer, result.response_id,
    )

    for call in result.tool_calls:
        await run_in_threadpool(
            db.save_tool_call,
            assistant_id, call.tool_type, call.queries, call.result_count,
        )

    citations_rows = [(c.file_id, c.filename) for c in result.citations]
    if citations_rows:
        await run_in_threadpool(db.save_citations, assistant_id, citations_rows)

    return assistant_id


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    user_id: str = Depends(require_user_id),
    rag: RagService = Depends(get_rag_service),
) -> ChatResponse:
    conversation_id, is_new = await _resolve_conversation(req, user_id)

    history = await run_in_threadpool(db.load_history_for_model, conversation_id)
    history.append({"role": "user", "content": req.message})

    try:
        result = await rag.answer(history)
    except Exception:
        # Full detail stays in server logs only — never exposed to client.
        logger.exception("rag.answer failed conversation_id=%s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream model error",
        )

    assistant_id = await _persist_turn(conversation_id, is_new, req.message, result)

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=assistant_id,
        answer=result.answer,
        tool_calls=[ToolCall(**tc.__dict__) for tc in result.tool_calls],
        citations=[Citation(**c.__dict__) for c in result.citations],
    )


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    user_id: str = Depends(require_user_id),
    rag: RagService = Depends(get_rag_service),
):
    """SSE endpoint. Emits events: text.delta, tool.searching, tool.searched, done, persisted, error."""
    conversation_id, is_new = await _resolve_conversation(req, user_id)

    history = await run_in_threadpool(db.load_history_for_model, conversation_id)
    history.append({"role": "user", "content": req.message})

    async def event_generator():
        from app.rag import AgentResult, CitationRecord, ToolCallRecord

        final_payload = None
        try:
            async for event in rag.answer_stream(history):
                if event["type"] == "done":
                    final_payload = event
                yield {"event": event["type"], "data": json.dumps(event)}
        except Exception:
            logger.exception("rag.answer_stream failed conversation_id=%s", conversation_id)
            yield {
                "event": "error",
                "data": json.dumps({"type": "error", "message": "upstream model error"}),
            }
            return

        if final_payload is None:
            return

        result = AgentResult(
            response_id=final_payload.get("response_id", ""),
            answer=final_payload.get("answer", ""),
            tool_calls=[ToolCallRecord(**tc) for tc in final_payload.get("tool_calls", [])],
            citations=[CitationRecord(**c) for c in final_payload.get("citations", [])],
        )
        assistant_id = await _persist_turn(conversation_id, is_new, req.message, result)

        yield {
            "event": "persisted",
            "data": json.dumps({
                "type": "persisted",
                "conversation_id": conversation_id,
                "message_id": assistant_id,
            }),
        }

    return EventSourceResponse(event_generator())
