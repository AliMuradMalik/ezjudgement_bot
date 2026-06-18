"""Cross-cutting ASGI middleware (request id, access logging)."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("ezjudgements.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id to every request and log latency."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request failed rid=%s method=%s path=%s elapsed_ms=%.1f",
                request_id, request.method, request.url.path, elapsed_ms,
            )
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "rid=%s method=%s path=%s status=%d elapsed_ms=%.1f",
            request_id, request.method, request.url.path,
            response.status_code, elapsed_ms,
        )
        return response
