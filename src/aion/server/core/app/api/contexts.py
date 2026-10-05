"""HTTP+JSON binding of the Context extension.

The same three methods the JSON-RPC dispatcher routes by method name, routed
here by path, relative to the agent's A2A endpoint:

=====================  ===============
``POST /contexts:get``  ``GetContexts``
``POST /context:get``   ``GetContext``
``POST /context:delete`` ``DeleteContext``
=====================  ===============

The request body is the method's parameter object and the success body its
result, exactly as in JSON-RPC. Failures are problem details
(``application/problem+json``): ``400`` for invalid parameters, the
lifecycle errors' own statuses (``404``, ``409``) with their ``contextId``,
``retryable`` and ``deletionOperationId`` fields, and ``400`` when the
extension cannot be served on this agent. Authentication is the server's
ordinary bearer-token middleware, which answers ``401`` before a route runs.

See: https://docs.aion.to/a2a/extensions/aion/context/1.0.0
"""

from __future__ import annotations

import json
import logging
from typing import Any

from a2a.server.routes.common import DefaultServerCallContextBuilder, ServerCallContextBuilder
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from aion.core.a2a import AION_JSONRPC_METHOD_EXTENSION_BINDINGS
from aion.core.constants.a2a import CONTEXT_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry
from aion.server.contexts import ContextLifecycleError

__all__ = ["CONTEXT_HTTP_PATHS", "ContextHTTPRoutes"]

logger = logging.getLogger(__name__)

CONTEXT_HTTP_PATHS: dict[str, str] = {
    "contexts:get": "GetContexts",
    "context:get": "GetContext",
    "context:delete": "DeleteContext",
}
"""HTTP+JSON path, relative to the A2A endpoint, for each Context method."""

_PROBLEM_JSON = "application/problem+json"


def _problem(status: int, title: str, detail: str | None = None, problem: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"title": title, "status": status}
    if problem is not None:
        body["type"] = f"{CONTEXT_EXTENSION_URI_V1}#{problem}"
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=_PROBLEM_JSON)


class ContextHTTPRoutes:
    """Registers the Context extension's HTTP+JSON routes on a FastAPI app.

    Args:
        request_handler: Answers the methods; the same handler the JSON-RPC
            dispatcher calls.
        base_url: The A2A endpoint the paths are relative to.
        context_builder: Builds the call context from the request, as the
            JSON-RPC dispatcher does.
    """

    def __init__(
        self,
        request_handler: Any,
        base_url: str = "/",
        context_builder: ServerCallContextBuilder | None = None,
    ) -> None:
        self._request_handler = request_handler
        self._base_url = base_url.rstrip("/")
        self._context_builder = context_builder or DefaultServerCallContextBuilder()

    def register(self, app: FastAPI) -> None:
        """Attach one ``POST`` route per Context method, listed in the OpenAPI schema."""
        for path, method in CONTEXT_HTTP_PATHS.items():
            app.add_api_route(
                f"{self._base_url}/{path}",
                self._endpoint(method),
                methods=["POST"],
                name=method,
            )

    def routes(self) -> list[Route]:
        """The same routes as plain Starlette routes, for an application that is not FastAPI."""
        return [
            Route(f"{self._base_url}/{path}", self._endpoint(method), methods=["POST"], name=method)
            for path, method in CONTEXT_HTTP_PATHS.items()
        ]

    def _endpoint(self, method: str):
        async def endpoint(request: Request) -> Response:
            return await self._handle(method, request)

        endpoint.__name__ = f"context_{method}"
        return endpoint

    async def _handle(self, method: str, request: Request) -> Response:
        binding = AION_JSONRPC_METHOD_EXTENSION_BINDINGS[method]
        unservable = aion_a2a_extension_registry.unservable_reason(binding.extension_uri)
        if unservable is not None:
            return _problem(400, "Unsupported operation", f"{method} is not available on this agent: {unservable}")

        raw = await request.body()
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError as error:
            return _problem(400, "Invalid JSON", str(error))
        try:
            params = binding.params_model.model_validate(body)
        except ValidationError as error:
            return _problem(400, "Invalid params", str(error))

        context = self._context_builder.build(request)
        context.state["method"] = method
        context.state["extension_uri"] = binding.extension_uri
        try:
            result = await getattr(self._request_handler, binding.handler_name)(params, context)
        except ContextLifecycleError as error:
            return JSONResponse(error.problem_details(), status_code=error.http_status, media_type=_PROBLEM_JSON)
        except Exception:
            logger.error("Unhandled exception in %s", method, exc_info=True)
            return _problem(500, "Internal error")
        return JSONResponse(result.model_dump(mode="json"))
