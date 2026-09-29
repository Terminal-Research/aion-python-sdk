"""Middleware that names every request's caller, and refuses one without a valid token when tokens are required."""

import logging
from typing import Optional

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from aion.server.auth import InvalidTokenError, TokenVerifier
from aion.server.constants import CONFIGURATION_FILE_URL, HEALTH_CHECK_URL
from fastapi import Request, Response
from starlette.authentication import AuthCredentials, UnauthenticatedUser
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

__all__ = [
    "AionAuthMiddleware",
    "PUBLIC_PATHS",
]

_BEARER = "bearer"

PUBLIC_PATHS = frozenset(
    path.rstrip("/") for path in (AGENT_CARD_WELL_KNOWN_PATH, HEALTH_CHECK_URL, CONFIGURATION_FILE_URL)
)
"""The only paths open without a token: how to call the agent, whether it is up, and its configuration schema."""


class AionAuthMiddleware(BaseHTTPMiddleware):
    """Names every request's caller; with a verifier, refuses one without a valid bearer token.

    The mode is the verifier it is given, which ``aion.server.auth.build_token_verifier``
    decides for ``AppFactory``: the platform's with ``AION_CLIENT_ID`` and
    ``AION_CLIENT_SECRET``, ``None`` without them.

    **With a verifier**, every request to the agent's server carries
    ``Authorization: Bearer <JWT>``, except the few ``PUBLIC_PATHS`` - the agent card, health and the
    configuration schema - which a client or a probe reads before it has a
    token. Everything else is closed by default: the JSON-RPC endpoint, the
    OpenAPI schema, and any route an application adds through ``AppRegistry``.
    The verifier checks the token, and the caller the token names is
    installed the way Starlette's authentication does - credentials in
    ``request.scope["auth"]``, the user in ``request.scope["user"]``. a2a-sdk's
    ``DefaultServerCallContextBuilder`` turns them into
    ``ServerCallContext.user``, and the agent's ``owner_resolver`` into the
    owner of the request's tasks and framework state.

    A request without a token, or with one that does not verify, is answered
    ``401`` before its body is read, and the agent never runs. Whatever a
    middleware in front of this one put in the scope is replaced.

    **Without a verifier** - local mode - nothing is checked and every request
    goes through. Its caller is the unauthenticated anonymous user, owner
    ``""``, unless a middleware in front of this one already named somebody.
    """

    def __init__(self, app: ASGIApp, verifier: Optional[TokenVerifier]) -> None:
        super().__init__(app)
        self._verifier = verifier

    async def dispatch(self, request: Request, call_next) -> Response:
        if self._verifier is None:
            request.scope.setdefault("auth", AuthCredentials())
            request.scope.setdefault("user", UnauthenticatedUser())
            return await call_next(request)

        if request.url.path.rstrip("/") in PUBLIC_PATHS:
            return await call_next(request)

        token = _bearer_token(request.headers.get("authorization"))
        if token is None:
            return _unauthorized("the request carries no bearer token", error_code=None)
        try:
            caller = self._verifier.verify(token)
        except InvalidTokenError as error:
            return _unauthorized(str(error), error_code="invalid_token")

        request.scope["auth"] = AuthCredentials(["authenticated"])
        request.scope["user"] = caller
        return await call_next(request)


def _bearer_token(header: str | None) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` header; ``None`` for anything else."""
    if not header:
        return None
    scheme, _, token = header.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != _BEARER or not token:
        return None
    return token


def _unauthorized(reason: str, *, error_code: str | None) -> JSONResponse:
    """A ``401`` in RFC 6750's form: the reason is safe to show, and never quotes the token."""
    logger.info("Refused a request: %s", reason)
    challenge = "Bearer" if error_code is None else f'Bearer error="{error_code}"'
    return JSONResponse(
        {"error": "unauthorized", "detail": reason},
        status_code=401,
        headers={"WWW-Authenticate": challenge},
    )
