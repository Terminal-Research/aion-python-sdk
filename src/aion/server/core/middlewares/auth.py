"""Middleware that names every request's caller from its bearer token, or as the stand-in caller."""

import logging
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from aion.server.auth import InvalidTokenError, TokenVerifier
from aion.server.auth.caller import AuthenticatedCaller
from aion.server.constants import CONFIGURATION_FILE_URL, HEALTH_CHECK_URL
from fastapi import Request, Response
from starlette.authentication import AuthCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

__all__ = [
    "AionAuthMiddleware",
    "PUBLIC_PATHS",
    "STAND_IN_SUBJECT",
]

STAND_IN_SUBJECT = "aion:anonymous:stand-in"
"""The caller of every request that carries no token that verifies; all such requests share its tasks."""

_BEARER = "bearer"

PUBLIC_PATHS = frozenset(
    path.rstrip("/") for path in (AGENT_CARD_WELL_KNOWN_PATH, HEALTH_CHECK_URL, CONFIGURATION_FILE_URL)
)
"""The only paths open without a token: how to call the agent, whether it is up, and its configuration schema."""


class AionAuthMiddleware(BaseHTTPMiddleware):
    """Names every request's caller from its bearer token, or as the stand-in caller.

    A request to the agent's server carries ``Authorization: Bearer <JWT>``,
    except the few ``PUBLIC_PATHS`` - the agent card, health and the
    configuration schema - which a client or a probe reads before it has a
    token and which pass untouched. The ``TokenVerifier`` ``AppFactory`` builds
    (``aion.server.auth.build_token_verifier``) checks the token, and the caller
    the token names is installed the way Starlette's authentication does -
    credentials in ``request.scope["auth"]``, the user in
    ``request.scope["user"]``. a2a-sdk's ``DefaultServerCallContextBuilder``
    turns them into ``ServerCallContext.user``, and the agent's
    ``owner_resolver`` into the owner of the request's tasks and framework
    state.

    A request without a token, or with one that does not verify, is served as
    ``STAND_IN_SUBJECT``, and a warning names the reason. Every such request
    shares that caller's tasks, so the server isolates only callers with a
    verified token. Whatever a middleware in front of this one put in the
    scope is replaced.
    """

    def __init__(self, app: ASGIApp, verifier: TokenVerifier) -> None:
        super().__init__(app)
        self._verifier = verifier

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.rstrip("/") in PUBLIC_PATHS:
            return await call_next(request)

        # TODO(platform-token-auth): answer these requests with `_unauthorized`
        # before release. The stand-in caller serves them until the control
        # plane publishes the keys that verify its tokens.
        token = _bearer_token(request.headers.get("authorization"))
        try:
            if token is None:
                raise InvalidTokenError("the request carries no bearer token")
            caller = await self._verifier.verify(token)
        except InvalidTokenError as error:
            logger.warning("Serving an unverified request as %s: %s", STAND_IN_SUBJECT, error)
            caller = AuthenticatedCaller(STAND_IN_SUBJECT, None, {})

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
