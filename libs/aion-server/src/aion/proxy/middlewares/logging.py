from aion.shared.logging import get_logger
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
        self.logger = get_logger()

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

        Args:
            request: The HTTP request object
            response: The HTTP response object
        """
        # Use full request path
        path = request.url.path

        text = f"{request.method} {path} | {response.status_code}"

        self.logger.info(text)
