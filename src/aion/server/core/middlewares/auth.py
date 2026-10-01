"""Middleware that names every request's caller, and refuses one it cannot name."""

import logging
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from aion.server.auth import (
    CallerCredentials,
    InvalidTokenError,
    KeysUnavailableError,
    TokenClaim,
    TokenVerifier,
    claimed_kind,
)
from aion.server.constants import CONFIGURATION_FILE_URL, HEALTH_CHECK_URL
from fastapi import Request, Response
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
    """Names every request's caller and refuses a request it cannot name.

    Every request to the agent's server needs a verified caller, except the
    few ``PUBLIC_PATHS`` - the agent card, health and the configuration schema
    - which a client or a probe reads before it has one. Everything else is
    closed by default: the JSON-RPC endpoint, the OpenAPI schema, and any route
    an application adds through ``AppRegistry``.

    The bearer token decides the path, by what it claims to be
    (``aion.server.auth.claimed_kind``), never by whether a check failed:

    - An Aion token (``typ`` ``aion-invocation+jwt`` or ``aion-session+jwt``)
      is verified by the ``TokenVerifier`` ``AppFactory`` builds. The caller it
      names replaces whatever user a middleware in front of this one set, and
      is installed the way Starlette's authentication does - credentials in
      ``request.scope["auth"]``, the user in ``request.scope["user"]``. The
      credentials carry the typed caller too (``CallerCredentials``), which is
      how request handling finds it behind a2a-sdk's user wrapper.
    - A token without an Aion ``typ`` but with an Aion ``token_use`` is a
      damaged Aion token and refused.
    - Anything else - no token, or the application's own credential - is
      served as the user the application's authentication installed, when
      the verifier trusts application users (outside the platform, without
      ``AION_REQUIRE_INVOCATION_AUTH``) and that user is authenticated with a
      non-empty ``display_name``. Its uniqueness is the application's
      contract. Otherwise the request is refused.

    a2a-sdk's ``DefaultServerCallContextBuilder`` turns the user into
    ``ServerCallContext.user``, and the agent's ``owner_resolver`` into the
    owner of the request's tasks and framework state. A refused request is
    answered ``401`` before its body is read, and the agent never runs. While
    the verification keys have not loaded, an Aion token cannot be checked at
    all and the request is answered ``503``: an outage never lets a token
    through unchecked.
    """

    def __init__(self, app: ASGIApp, verifier: TokenVerifier) -> None:
        super().__init__(app)
        self._verifier = verifier

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.rstrip("/") in PUBLIC_PATHS:
            return await call_next(request)

        token = _bearer_token(request.headers.get("authorization"))
        claim = claimed_kind(token) if token is not None else TokenClaim.FOREIGN
        if claim is TokenClaim.DAMAGED_AION:
            return _unauthorized("the bearer token is a damaged Aion token", error_code="invalid_token")
        if claim is TokenClaim.FOREIGN:
            if self._verifier.trust_application_users and _is_application_user(request.scope.get("user")):
                return await call_next(request)
            if token is None:
                return _unauthorized("the request carries no bearer token", error_code=None)
            return _unauthorized("the bearer token is not an Aion token", error_code="invalid_token")

        try:
            caller = await self._verifier.verify(token)
        except InvalidTokenError as error:
            return _unauthorized(str(error), error_code="invalid_token")
        except KeysUnavailableError as error:
            return _unavailable(str(error))

        request.scope["auth"] = CallerCredentials(caller)
        request.scope["user"] = caller
        return await call_next(request)


def _is_application_user(user: object) -> bool:
    """Whether the application's authentication installed a user to serve the request as."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    name = getattr(user, "display_name", None)
    return isinstance(name, str) and bool(name.strip())


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


def _unavailable(reason: str) -> JSONResponse:
    """A ``503``: the token could not be checked yet, so the request is neither refused nor served."""
    logger.warning("Could not verify a request: %s", reason)
    return JSONResponse(
        {"error": "unavailable", "detail": reason},
        status_code=503,
        headers={"Retry-After": "1"},
    )
