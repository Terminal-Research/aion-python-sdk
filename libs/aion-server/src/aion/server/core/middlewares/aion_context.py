from typing import Any

from a2a.server.jsonrpc_models import InternalError, InvalidRequestError
from a2a.server.request_handlers import build_error_response
from a2a.utils import DEFAULT_RPC_URL
from aion.shared.constants import (
    DISTRIBUTION_EXTENSION_URI_V1,
    TRACEABILITY_EXTENSION_URI_V1,
)
from aion.shared.context import set_context_from_a2a
from aion.shared.logging import get_logger
from aion.shared.types.a2a.extensions.distribution import DistributionExtensionV1
from aion.shared.types.a2a.extensions.traceability import TraceabilityExtensionV1
from fastapi import Request, Response
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = get_logger(use_logstash=False)

__all__ = [
    "AionContextMiddleware",
]


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
        method, request_id, metadata = await self._extract_method_and_metadata(request)

        if metadata:
            try:
                set_context_from_a2a(
                    distribution=self._get_distribution_extension(metadata),
                    traceability=self._get_traceability_extension(metadata),
                    request_method=request.method,
                    request_path=request.url.path,
                    jrpc_method=method,
                )
            except ValidationError as ex:
                logger.warning("Invalid extension data in request metadata: %s", ex)
                return JSONResponse(
                    build_error_response(request_id, InvalidRequestError(data=str(ex))),
                    status_code=200,
                )
            except Exception as ex:
                logger.exception("Failed to set request context: %s", ex)
                return JSONResponse(
                    build_error_response(request_id, InternalError()),
                    status_code=200,
                )

        return await call_next(request)

    @staticmethod
    async def _extract_method_and_metadata(
            request: Request,
    ) -> tuple[str | None, str | int | None, dict[str, Any] | None]:
        """Extract JSON-RPC method name and params.metadata from the raw body.

        Does not perform full A2A schema validation — only accesses the fields
        this middleware actually needs.

        Returns:
            (method, metadata) tuple; both None if the body is not a valid
            JSON-RPC dict or has no metadata.
        """
        try:
            body = await request.json()
            if not isinstance(body, dict):
                return None, None, None

            method = body.get('method')
            request_id = body.get('id')
            params = body.get('params')
            metadata = params.get('metadata') if isinstance(params, dict) else None
            return method, request_id, metadata
        except Exception:
            return None, None, None

    @staticmethod
    def _get_distribution_extension(metadata: dict[str, Any]) -> DistributionExtensionV1 | None:
        """
        Extract the distribution extension from A2A metadata.

        Tries known version URIs in precedence order, so future versions
        can be added here without touching call sites.
        """
        raw = metadata.get(DISTRIBUTION_EXTENSION_URI_V1)
        if raw is None:
            return None
        return DistributionExtensionV1.model_validate(raw)

    @staticmethod
    def _get_traceability_extension(metadata: dict[str, Any]) -> TraceabilityExtensionV1 | None:
        """
        Extract the traceability extension from A2A metadata.

        Tries known version URIs in precedence order, so future versions
        can be added here without touching call sites.
        """
        raw = metadata.get(TRACEABILITY_EXTENSION_URI_V1)
        if raw is None:
            return None
        return TraceabilityExtensionV1.model_validate(raw)
