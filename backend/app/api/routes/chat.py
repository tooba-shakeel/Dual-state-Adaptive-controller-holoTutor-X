"""
Chat API Routes — handling conversations, streaming SSE, and search.

Every route needs a login. A user sees and continues only their own conversations
and searches only their own documents; another user's conversation is reported
as not found.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.routes.auth import current_user
from app.core.models import ChatRequest, Conversation
from app.dependencies import get_chat_service, get_conversation_store, get_document_service
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


async def owned_conversation(conversation_id: str, user: dict[str, Any], store) -> Conversation:
    """The conversation if it belongs to the user, else 404."""
    conv = await store.get_conversation(conversation_id)
    if not conv or conv.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.post("/chat")
async def chat(
    request: ChatRequest,
    user: dict[str, Any] = Depends(current_user),
    chat_service: ChatService = Depends(get_chat_service),
    store=Depends(get_conversation_store),
) -> StreamingResponse:
    """
    Stream a chat response via Server-Sent Events (SSE).
    The first event will contain citations metadata, the conversation id and the Tutor State.
    Subsequent events contain text chunks.
    """
    if request.conversation_id:
        await owned_conversation(request.conversation_id, user, store)
    allowed = await get_document_service().document_ids_of(user["id"])

    async def sse_generator() -> AsyncGenerator[str, None]:
        try:
            async for chunk, citations, meta in chat_service.chat_stream(
                request, user_id=user["id"], allowed_document_ids=allowed
            ):
                # Build an SSE payload
                data = {
                    "text": chunk,
                }
                if citations:
                    data["citations"] = [c.model_dump() for c in citations]
                if meta:
                    data["conversation_id"] = meta["conversation_id"]
                    data["topic"] = meta["topic"]
                    data["tutor_state"] = meta["tutor_state"].model_dump(mode="json")

                yield f"data: {json.dumps(data)}\n\n"

            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            logger.exception("Error in chat streaming")
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable NGINX buffering
        },
    )


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    store=Depends(get_conversation_store),
):
    """List the logged-in user's previous conversations."""
    convs, _ = await store.list_conversations(limit=limit, offset=offset, user_id=user["id"])
    return convs


@router.post("/conversations", response_model=Conversation)
async def create_conversation(
    title: str = Query("New Conversation"),
    user: dict[str, Any] = Depends(current_user),
    store=Depends(get_conversation_store),
):
    """Create a new, empty conversation for the logged-in user."""
    conv = await store.create_conversation(title=title, user_id=user["id"])
    return conv


@router.get("/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(
    conversation_id: str,
    user: dict[str, Any] = Depends(current_user),
    store=Depends(get_conversation_store),
):
    """Retrieve full conversation history (own conversations only)."""
    return await owned_conversation(conversation_id, user, store)


@router.get("/search")
async def search(
    query: str,
    top_k: int = Query(5, ge=1, le=20),
    document_ids: list[str] = Query(None),
    user: dict[str, Any] = Depends(current_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    """Direct semantic search over the logged-in user's own documents."""
    allowed = await get_document_service().document_ids_of(user["id"])
    ids = [d for d in (document_ids or allowed) if d in allowed]
    if not ids:
        return {"results": []}
    citations = await chat_service._retriever.retrieve(
        query=query,
        top_k=top_k,
        document_ids=ids,
    )
    return {"results": [c.model_dump() for c in citations]}
