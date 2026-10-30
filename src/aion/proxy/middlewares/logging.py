"""Request logging middleware for the Aion proxy."""

import json
import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from aion.proxy.constants import HEALTH_CHECK_URL, SYSTEM_HEALTH_CHECK_URL

__all__ = ["ProxyLoggingMiddleware"]

# A healthy answer to a probe is the one request that says nothing about the
# proxy's work: the platform sends one every 30 s, and recording each would be
# the bulk of the log. A probe that fails is logged like any other failure.
_QUIET_PATHS = frozenset({HEALTH_CHECK_URL, SYSTEM_HEALTH_CHECK_URL})


def _log_value(value: str) -> str:
    """Bound caller-controlled values and keep each log entry on one line."""
    return "".join(char if char.isprintable() else " " for char in value[:512])


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
        A successful health probe is not logged at all.

        Args:
            request: The HTTP request object
            response: The HTTP response object
        """
        if request.url.path in _QUIET_PATHS and response.status_code == 200:
            return
        # Use a separate namespace from Logstash's HTTP ingestion metadata.
        # The ASGI client may already have been rewritten by trusted proxy
        # middleware; forwarded headers are caller claims, not verified identity.
        details = {
            "method": _log_value(request.method),
            "path": _log_value(request.url.path),
            "status_code": response.status_code,
        }
        if request.client is not None:
            details["client_host"] = _log_value(request.client.host)
            details["client_port"] = request.client.port
        for header, field in (
            ("user-agent", "user_agent"),
            ("host", "host"),
            ("x-forwarded-for", "forwarded_for"),
            ("x-real-ip", "real_ip"),
        ):
            value = request.headers.get(header)
            if value is not None:
                details[field] = _log_value(value)
        # Include diagnostics in text too, for terminal and message-only readers.
        # Do not collect query strings, bodies, cookies, or authorization headers.
        text = (f"{details['method']} {details['path']} | {response.status_code} "
                f"| proxy_request={json.dumps(details, ensure_ascii=True)}")
        extra = {"proxy_request": details}

        logged_elsewhere = getattr(request.state, "logged_elsewhere", False)
        if response.status_code >= 400 and not logged_elsewhere:
            self.logger.warning(text, extra=extra)
        else:
            self.logger.debug(text, extra=extra)
