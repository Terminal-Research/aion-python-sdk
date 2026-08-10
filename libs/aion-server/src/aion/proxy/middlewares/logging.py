"""Request logging middleware for the Aion proxy."""

import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

__all__ = ["ProxyLoggingMiddleware"]


class ProxyLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs each HTTP request passing through the proxy.

    This middleware logs both incoming requests and outgoing responses
    with relevant metadata like agent_id, path, method, and status code.

    Args:
        app: The ASGI application
    """

    def __init__(self, app):
        super().__init__(app)
        self.logger = logging.getLogger(__name__)

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request and log response information.

        Logs the response status after the request is processed.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            HTTP response from the application
        """
        # Process request
        response = await call_next(request)

        # Log response
        self._log_request_response(request, response)

        return response

    def _log_request_response(self, request: Request, response: Response):
        """Log proxy request completion with status code.

        A request the proxy forwards is logged again by the agent that serves it,
        so announcing every hop at info level doubles the request log for nothing.
        Failures are the exception: a request the proxy rejects, or one it cannot
        deliver, never reaches an agent and this is the only record it leaves.

        Args:
            request: The HTTP request object
            response: The HTTP response object
        """
        # Use full request path
        path = request.url.path

        text = f"{request.method} {path} | {response.status_code}"

        if response.status_code >= 400:
            self.logger.warning(text)
        else:
            self.logger.debug(text)
