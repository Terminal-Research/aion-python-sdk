from a2a.types import JSONRPCRequest
from a2a.utils import DEFAULT_RPC_URL
from aion.shared.agent.execution import set_context_from_a2a_request
from aion.shared.logging import get_logger
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

__all__ = [
    "AionContextMiddleware",
]

from aion.shared.types import ExtendedA2ARequest

logger = get_logger(use_logstash=False)


class AionContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware for extracting and setting context from A2A requests.

    Intercepts JSON-RPC POST requests to the default RPC URL and extracts
    metadata from the request to set up the request context for logging
    and tracing purposes.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == DEFAULT_RPC_URL and request.method == "POST":
            return await self.dispatch_rpc_post(request, call_next)
        return await call_next(request)

    async def dispatch_rpc_post(self, request: Request, call_next) -> Response:
        """
        Handle JSON-RPC POST requests and extract metadata for context.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or handler in the chain

        Returns:
            Response from the next handler in the chain
        """
        request_obj = await self._get_request_object(request)
        if not request_obj:
            return await call_next(request)

        try:
            if request_obj.params.metadata:
                set_context_from_a2a_request(
                    metadata=request_obj.params.metadata,
                    request_method=request.method,
                    request_path=request.url.path,
                    jrpc_method=request_obj.method
                )
        except Exception as ex:
            logger.exception(f"Error while setting request context: {ex}")

        return await call_next(request)

    @staticmethod
    async def _get_request_object(request: Request):
        """
        Parse and validate the request body as an A2A request.

        Args:
            request: The incoming HTTP request

        Returns:
            Validated A2A request object or None if validation fails
        """
        try:
            body = await request.json()
            JSONRPCRequest.model_validate(body)
            a2a_request = ExtendedA2ARequest.model_validate(body)
            return a2a_request.root
        except Exception:
            return None
