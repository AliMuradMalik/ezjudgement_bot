"""Health endpoints. Unauthenticated — safe for load balancer probes."""

import time

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.schemas import DbHealthResponse, HealthResponse
from database import db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        version=settings.app_version,
    )


@router.get("/db", response_model=DbHealthResponse)
async def db_health() -> DbHealthResponse:
    start = time.perf_counter()
    try:
        await run_in_threadpool(db.ping)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unreachable: {exc}",
        )
    return DbHealthResponse(
        status="ok",
        latency_ms=(time.perf_counter() - start) * 1000,
    )
