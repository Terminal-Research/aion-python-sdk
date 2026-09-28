"""Middleware that names the caller of an A2A request in the ASGI scope."""

from aion.server.identity import CallerResolver, resolve_distribution_caller
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ._rpc_request import (
    InvalidExtensionPayloadError,
    is_rpc_post,
    prepare_rpc_request,
    refuse_invalid_extension,
)

__all__ = [
    "CallerIdentityMiddleware",
]


class CallerIdentityMiddleware(BaseHTTPMiddleware):
    """Installs the caller of an A2A JSON-RPC request as ``scope["auth"]`` and ``scope["user"]``.

    A request whose ``params.metadata`` carries a distribution payload is
    that distribution's: the middleware asks its ``CallerResolver`` - by
    default ``resolve_distribution_caller``, which vouches for nobody - for
    the credentials and the user, and installs the two together, the way
    Starlette's ``AuthenticationMiddleware`` does. a2a-sdk's
    ``DefaultServerCallContextBuilder`` turns them into
    ``ServerCallContext.user`` and ``state["auth"]``, and the agent's
    ``owner_resolver`` into the owner of the request's tasks and framework
    state.

    Precedence: a distribution payload decides the caller, whatever user and
    credentials a middleware that ran before this one left in the scope. Both
    are replaced, so a distribution never carries another caller's
    credentials. A request without a payload keeps the scope as it found it:
    an application's user and credentials, or none, which the builder makes
    the anonymous caller ``""``. Requests to anything but the JSON-RPC
    endpoint pass untouched. ``AppFactory`` installs this outside the
    server's other middlewares; middleware running inside it could still
    rewrite the scope.

    A malformed distribution payload is refused as an invalid request rather
    than served anonymously.
    """

    def __init__(
            self,
            app: ASGIApp,
            resolve_caller: CallerResolver = resolve_distribution_caller,
    ) -> None:
        super().__init__(app)
        self._resolve_caller = resolve_caller

    async def dispatch(self, request: Request, call_next) -> Response:
        if not is_rpc_post(request):
            return await call_next(request)

        try:
            prepared = await prepare_rpc_request(request)
        except InvalidExtensionPayloadError as refusal:
            return refuse_invalid_extension(refusal.request_id, refusal.detail)

        if prepared.distribution is not None:
            request.scope["auth"], request.scope["user"] = await self._resolve_caller(
                request, prepared.distribution
            )
        return await call_next(request)
