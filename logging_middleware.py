import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars

from logger import get_logger

log = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        bind_contextvars(
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )

        log.info("request.started")
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request.failed")
            raise
        else:
            log.info("request.completed", http_status_code=response.status_code)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            clear_contextvars()
