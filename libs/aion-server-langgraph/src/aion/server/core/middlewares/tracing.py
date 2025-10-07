from __future__ import annotations

from typing import TYPE_CHECKING

from aion.shared.context import get_context
from aion.shared.logging import get_logger
from aion.shared.opentelemetry import generate_request_span_context
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:
    from aion.shared.context import RequestContext

__all__ = ["TracingMiddleware"]


class TracingMiddleware(BaseHTTPMiddleware):
    """Middleware that creates OpenTelemetry span for each HTTP request.

    This middleware ensures that a tracing span is active during the entire
    request lifecycle.
    It also logs request completion with transaction name and status code.

    Args:
        app: The ASGI application
        tracer_name: Name of the OpenTelemetry tracer to use.
                     Defaults to "LanggraphAgentRequest"
    """

    def __init__(self, app, tracer_name: str = "LanggraphAgentRequest"):
        super().__init__(app)
        self.tracer = trace.get_tracer(tracer_name)
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
        request_context: RequestContext = get_context()
        with self.tracer.start_as_current_span(
                name="a2a.process_request",
                context=generate_request_span_context(trace_id=getattr(request_context, "trace_id", None))
        ):
            response = await call_next(request)
            self._log_request(request, response, request_context)
            return response

    def _log_request(self, request: Request, response: Response, request_context):
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
