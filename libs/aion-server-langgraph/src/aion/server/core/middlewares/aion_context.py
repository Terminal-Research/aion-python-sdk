from a2a.types import JSONRPCRequest
from a2a.utils import DEFAULT_RPC_URL
from aion.shared.context import set_context_from_a2a_request
from aion.shared.logging import get_logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

__all__ = [
    "AionContextMiddleware",
]

from aion.server.types import ExtendedA2ARequest

logger = get_logger(use_logstash=False)


class AionContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == DEFAULT_RPC_URL and request.method == "POST":
            return await self.dispatch_rpc_post(request, call_next)
        return await call_next(request)

    async def dispatch_rpc_post(self, request: Request, call_next) -> Response:
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
        try:
            body = await request.json()
            JSONRPCRequest.model_validate(body)
            a2a_request = ExtendedA2ARequest.model_validate(body)
            return a2a_request.root
        except Exception:
            return None
