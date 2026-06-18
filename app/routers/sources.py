"""Serve the original source PDFs that back the vector store.

Each case has two variants — ``judgment`` (full text) and ``headnote``
(summary) — that share a filename, so the variant is part of the path:

    GET /sources/judgment/CLC2013K219.pdf
    GET /sources/headnote/CLC2013K219.pdf

Unauthenticated: the documents are the same corpus the bot answers from, and
the URL is only discoverable via a citation the model already returned.
"""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.sources import KINDS, resolve_source

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/{kind}/{filename:path}")
async def get_source(kind: str, filename: str) -> FileResponse:
    if kind not in KINDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown document type",
        )
    path = resolve_source(filename, kind)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="source document not found",
        )
    # inline so the browser opens its PDF viewer instead of downloading.
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
    )
