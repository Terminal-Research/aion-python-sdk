"""An endpoint that reports what the server's middlewares made of a request.

``Probe.rpc`` stands where a2a-sdk's JSON-RPC dispatcher stands and reads the
request the way it and the default agent do - through
``DefaultServerCallContextBuilder`` and ``resolve_user_scope`` - so a test
sees the owner, the user and the credentials a real request would carry, and
the distribution the execution scope holds. ``Probe.elsewhere`` is any other
route, and reports the scope as it reached it.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.routes import DefaultServerCallContextBuilder
from a2a.utils import DEFAULT_RPC_URL
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from aion.server.agent.execution.scope import get_execution_scope

ELSEWHERE = "/elsewhere"


class Probe:
    """The two routes, counting the requests that reach the JSON-RPC one."""

    def __init__(self) -> None:
        self.calls = 0

    async def rpc(self, request: Request) -> JSONResponse:
        self.calls += 1
        # The dispatcher reads the body after the middlewares have.
        await request.json()
        context = DefaultServerCallContextBuilder().build(request)
        credentials = context.state.get("auth")
        scope = get_execution_scope()
        return JSONResponse(
            {
                "owner": resolve_user_scope(context),
                "authenticated": context.user.is_authenticated,
                "scopes": None if credentials is None else list(credentials.scopes),
                "scope_distribution": scope.inbound.aion.distribution_id if scope else None,
            }
        )

    async def elsewhere(self, request: Request) -> JSONResponse:
        user = request.scope.get("user")
        credentials = request.scope.get("auth")
        return JSONResponse(
            {
                "user": None if user is None else user.display_name,
                "scopes": None if credentials is None else list(credentials.scopes),
            }
        )

    def client(self, *middleware: Middleware) -> httpx.AsyncClient:
        """A client for both routes behind ``middleware``, outermost first."""
        app = Starlette(
            routes=[
                Route(DEFAULT_RPC_URL, self.rpc, methods=["POST"]),
                Route(ELSEWHERE, self.elsewhere, methods=["POST"]),
            ],
            middleware=list(middleware),
        )
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def send_message(
    client: httpx.AsyncClient,
    metadata: Optional[dict[str, Any]] = None,
    *,
    request_id: Any = "req-1",
    headers: Optional[dict[str, str]] = None,
    path: str = DEFAULT_RPC_URL,
) -> httpx.Response:
    """Post a ``SendMessage`` call, with ``metadata`` as its ``params.metadata``."""
    params: dict[str, Any] = {
        "message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "hi"}]},
    }
    if metadata is not None:
        params["metadata"] = metadata
    return await client.post(
        path,
        json={"jsonrpc": "2.0", "id": request_id, "method": "SendMessage", "params": params},
        headers=headers or {},
    )
