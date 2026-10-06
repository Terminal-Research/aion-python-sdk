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

        A response the agent produced is relayed as is and the agent has logged
        that request, and a forwarding failure is logged with its cause by the
        handler; both set ``request.state.logged_elsewhere`` and stay at debug
        here, whatever the status. Any other failure is the proxy's own and
        never reached an agent, so this line is its only record, at warning.

        Args:
            request: The HTTP request object
            response: The HTTP response object
        """
        text = f"{request.method} {request.url.path} | {response.status_code}"

        logged_elsewhere = getattr(request.state, "logged_elsewhere", False)
        if response.status_code >= 400 and not logged_elsewhere:
            self.logger.warning(text)
        else:
            self.logger.debug(text)
