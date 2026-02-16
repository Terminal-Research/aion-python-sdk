from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.utils import DEFAULT_RPC_URL
from a2a.utils.telemetry import trace_function
from aion.shared.context import get_context
from aion.shared.logging import get_logger
from aion.shared.opentelemetry import generate_request_span_context
from fastapi import Request, Response
from opentelemetry import context
from opentelemetry.trace import SpanKind
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from aion.shared.context import ExecutionContext


__all__ = ["TracingMiddleware"]


class TracingMiddleware(BaseHTTPMiddleware):
    """Middleware that creates OpenTelemetry span for each HTTP request.

    This middleware ensures that a tracing span is active during the entire
    request lifecycle.
    It also logs request completion with transaction name and status code.

    Args:
        app: The ASGI application
    """

    def __init__(self, app):
        super().__init__(app)
        self.logger = get_logger()

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with active tracing span.

        Creates an OpenTelemetry span for the request using the trace_id
        from the request context if available. Logs the request completion
        after the response is generated.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            HTTP response from the application
        """
        request_context: ExecutionContext = get_context()
        if request_context:
            d1=1

        # Generate trace context and attach it globally
        trace_context = generate_request_span_context(
            trace_id=request_context.trace.trace_id if request_context else None,
            span_id=request_context.trace.span_id if request_context else None
        )
        if trace_context:
            token = context.attach(trace_context)
        else:
            token = None

        try:
            return await self.dispatch_request(request, request_context, call_next)
        finally:
            if token is not None:
                context.detach(token)

    @trace_function(kind=SpanKind.SERVER)
    async def dispatch_request(self, request, request_context, call_next):
        self._log_request_received(request, request_context)
        response = await call_next(request)
        self._log_request_response(request, response, request_context)
        return response

    def _log_request_received(self, request: Request, request_context):
        """Log incoming request.

        Args:
            request: The HTTP request object
            request_context: Request context containing transaction metadata
        """
        if request.url.path == DEFAULT_RPC_URL and request.method == "POST":
            if request_context:
                text = f"Received RPC request: {request_context.transaction_name}"
            else:
                text = f"Received RPC request: {request.method} {request.url.path}"

            self.logger.info(text)

    def _log_request_response(self, request: Request, response: Response, request_context):
        """Log request completion with transaction name and status code.

        Uses transaction name from request context if available,
        otherwise falls back to HTTP method and path.

        Args:
            request: The HTTP request object
            response: The HTTP response object
            request_context: Request context containing transaction metadata
        """
        if request_context:
            text = f"{request_context.transaction_name} | {response.status_code}"
        else:
            text = f"{request.method} {request.url.path} | {response.status_code}"

        self.logger.info(text)
