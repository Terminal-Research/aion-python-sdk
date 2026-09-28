"""Middleware that extracts A2A request metadata and populates the execution scope."""

import logging
from typing import Any

from a2a.server.jsonrpc_models import InternalError
from a2a.server.request_handlers import build_error_response
from aion.core.constants import TRACEABILITY_EXTENSION_URI_V1
from aion.server.agent.execution.scope import (
    init_execution_scope,
    set_distribution,
    set_traceability,
    set_request,
)
from aion.core.a2a.extensions.traceability import TraceabilityExtensionV1
from fastapi import Request, Response
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ._rpc_request import (
    InvalidExtensionPayloadError,
    describe_validation_error,
    is_rpc_post,
    prepare_rpc_request,
    refuse_invalid_extension,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AionContextMiddleware",
]


class AionContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware for extracting and setting context from A2A requests.

    Intercepts JSON-RPC POST requests to the default RPC URL and extracts
    metadata from the request to set up the request context for logging
    and tracing purposes.

    Reads the request as ``prepare_rpc_request`` prepared it. Behind
    ``CallerIdentityMiddleware`` that is the preparation the identity
    middleware already made; on its own this middleware prepares the request
    itself, the same way, and refuses a malformed distribution payload the
    same way.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if is_rpc_post(request):
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
        try:
            prepared = await prepare_rpc_request(request)
        except InvalidExtensionPayloadError as refusal:
            return refuse_invalid_extension(refusal.request_id, refusal.detail)

        try:
            # Initialize scope and populate from A2A extensions
            init_execution_scope()
            if prepared.distribution is not None:
                set_distribution(prepared.distribution)
            if prepared.metadata:
                if traceability := self._get_traceability_extension(prepared.metadata):
                    set_traceability(traceability)
            set_request(request.method, request.url.path, prepared.method)
        except ValidationError as ex:
            return refuse_invalid_extension(prepared.request_id, describe_validation_error(ex))
        except Exception as ex:
            logger.exception("Failed to set request context: %s", ex)
            return JSONResponse(
                build_error_response(prepared.request_id, InternalError()),
                status_code=200,
            )

        return await call_next(request)

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
