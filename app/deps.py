"""FastAPI dependency providers: user scoping + services."""

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.rag import RagService


async def require_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> str:
    """Device/session identity — a UUID the frontend mints once and keeps in localStorage.

    Not real authentication. Treat it as a pseudonymous session id. The only
    safety it provides is per-user scoping on conversation endpoints so clients
    cannot read or delete each other's chats.
    """
    if not x_user_id or len(x_user_id) < 8 or len(x_user_id) > 256:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing or invalid X-User-ID header",
        )
    return x_user_id


def get_rag_service(settings: Settings = Depends(get_settings)) -> RagService:
    return RagService(
        api_key=settings.openai_api_key,
        vector_store_id=settings.vector_store_id,
        model=settings.model,
        max_num_results=settings.max_num_results,
    )
