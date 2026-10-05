"""Middleware that names every request's caller, and refuses one it cannot name."""

import logging
from collections.abc import Sequence
from enum import Enum
from typing import Any, Optional

from a2a.server.jsonrpc_models import InternalError
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, DEFAULT_RPC_URL
from aion.core.constants.a2a import CONTEXT_HTTP_PATHS
from aion.server.auth import (
    CallerCredentials,
    InvalidTokenError,
    KeysUnavailableError,
    MAX_TOKEN_BYTES,
    TokenClaim,
    TokenVerifier,
    claimed_kind,
)
from aion.server.constants import CONFIGURATION_FILE_URL, HEALTH_CHECK_URL
from aion.server.core.errors import AUTHENTICATION_REQUIRED_CODE, AuthenticationRequired
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Scope

logger = logging.getLogger(__name__)

__all__ = [
    "AionAuthMiddleware",
    "PUBLIC_PATHS",
    "Transport",
    "transport_of",
]

_BEARER = "bearer"
_PROBLEM_JSON = "application/problem+json"

PUBLIC_PATHS = frozenset(
    path.rstrip("/") for path in (AGENT_CARD_WELL_KNOWN_PATH, HEALTH_CHECK_URL, CONFIGURATION_FILE_URL)
)
"""The only paths open without a token: how to call the agent, whether it is up, and its configuration schema."""

_JSONRPC_PATH = DEFAULT_RPC_URL.rstrip("/")
_CONTEXT_HTTP_PATHS = frozenset(f"{_JSONRPC_PATH}/{path}" for path in CONTEXT_HTTP_PATHS)


class Transport(Enum):
    """The protocol binding a request arrives on, which decides the form of a refusal's body."""

    JSONRPC = "jsonrpc"
    """The A2A JSON-RPC endpoint: a JSON-RPC response object."""
    HTTP_JSON = "http+json"
    """A Context extension HTTP+JSON route: an ``application/problem+json`` problem detail."""
    OTHER = "other"
    """Any other closed path, such as the OpenAPI schema: a plain JSON object."""


def transport_of(path: str) -> Transport:
    """The transport whose error format a refusal on ``path`` is written in, trailing slash ignored."""
    path = path.rstrip("/")
    if path == _JSONRPC_PATH:
        return Transport.JSONRPC
    if path in _CONTEXT_HTTP_PATHS:
        return Transport.HTTP_JSON
    return Transport.OTHER


class AionAuthMiddleware(BaseHTTPMiddleware):
    """Names every request's caller and refuses a request it cannot name.

    Every request to the agent's server needs a verified caller, except the
    few ``PUBLIC_PATHS`` - the agent card, health and the configuration schema
    - which a client or a probe reads before it has one, and the routes the
    application owns. Everything else is closed by default: the JSON-RPC
    endpoint, the OpenAPI schema, and any route the SDK or a plugin adds.

    The application's routes are those ``AppFactory`` mounts from
    ``AppRegistry`` (``application_routes``). Who may call them is the
    application's to decide: this middleware passes their requests on
    untouched, without reading their token or installing a caller. A request
    is the application's when the route the server would dispatch it to is
    one of them - the same choice Starlette's router makes, first full match,
    else first partial one - so a route of the application never opens a
    path the server answers with one of its own.

    A bearer token longer than ``MAX_TOKEN_BYTES`` (8 KiB, in UTF-8) is
    refused before anything reads it, whoever issued it: an application's own
    credential too, and its user is not served instead.

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

    A refusal keeps its HTTP status and headers - ``401`` with an RFC 6750
    ``WWW-Authenticate`` challenge, ``503`` with ``Retry-After`` - and its
    body is written in the error format of the transport the path belongs to
    (``transport_of``):

    - the JSON-RPC endpoint answers a JSON-RPC error with ``id`` ``null``,
      since the body is never read: ``AUTHENTICATION_REQUIRED_CODE``
      (``-32051``) for a ``401``, and ``-32603`` with ``retryable`` ``true``
      in its ``data`` for a ``503``; the reason is ``data.detail``;
    - the Context extension's HTTP+JSON routes answer an
      ``application/problem+json`` problem detail with the reason as
      ``detail``, and no ``type``, which the specification does not define
      for these statuses;
    - every other closed path answers ``{"error": ..., "detail": ...}``.
    """

    def __init__(
        self, app: ASGIApp, verifier: TokenVerifier, application_routes: Sequence[BaseRoute] = ()
    ) -> None:
        """
        Args:
            app: The application this middleware wraps.
            verifier: Verifies Aion's tokens in the server's mode.
            application_routes: The routes the application owns, open to its
                own policy. Read on every request: ``AppFactory`` adds to it
                when it mounts the ``AppRegistry`` routers, after the
                middleware is installed.
        """
        super().__init__(app)
        self._verifier = verifier
        self._application_routes = application_routes

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.rstrip("/") in PUBLIC_PATHS or self._is_application_route(request.scope):
            return await call_next(request)

        path = request.url.path
        token = _bearer_token(request.headers.get("authorization"))
        if token is not None and len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
            return _unauthorized(
                path, f"the bearer token is longer than {MAX_TOKEN_BYTES} bytes", error_code="invalid_token"
            )
        claim = claimed_kind(token) if token is not None else TokenClaim.FOREIGN
        if claim is TokenClaim.DAMAGED_AION:
            return _unauthorized(path, "the bearer token is a damaged Aion token", error_code="invalid_token")
        if claim is TokenClaim.FOREIGN:
            if self._verifier.trust_application_users and _is_application_user(request.scope.get("user")):
                return await call_next(request)
            if token is None:
                return _unauthorized(path, "the request carries no bearer token", error_code=None)
            return _unauthorized(path, "the bearer token is not an Aion token", error_code="invalid_token")

        try:
            caller = await self._verifier.verify(token)
        except InvalidTokenError as error:
            return _unauthorized(path, str(error), error_code="invalid_token")
        except KeysUnavailableError as error:
            return _unavailable(path, str(error))

        request.scope["auth"] = CallerCredentials(caller)
        request.scope["user"] = caller
        return await call_next(request)

    def _is_application_route(self, scope: Scope) -> bool:
        """Whether the server would hand the request to a route the application owns."""
        if not self._application_routes:
            return False
        app: Any = scope.get("app")
        router = getattr(app, "router", None)
        if router is None:
            return False
        route = _dispatched_route(router.routes, scope)
        return route is not None and any(route is owned for owned in self._application_routes)


def _is_application_user(user: object) -> bool:
    """Whether the application's authentication installed a user to serve the request as."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    name = getattr(user, "display_name", None)
    return isinstance(name, str) and bool(name.strip())


def _dispatched_route(routes: Sequence[BaseRoute], scope: Scope) -> Optional[BaseRoute]:
    """The route Starlette's router hands the request to: the first full match, else the first partial one."""
    partial = None
    for route in routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return route
        if match is Match.PARTIAL and partial is None:
            partial = route
    return partial


def _bearer_token(header: str | None) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` header; ``None`` for anything else."""
    if not header:
        return None
    scheme, _, token = header.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != _BEARER or not token:
        return None
    return token


def _unauthorized(path: str, reason: str, *, error_code: str | None) -> JSONResponse:
    """A ``401`` in RFC 6750's form: the reason is safe to show, and never quotes the token."""
    logger.info("Refused a request: %s", reason)
    challenge = "Bearer" if error_code is None else f'Bearer error="{error_code}"'
    return _refusal(
        path,
        401,
        reason,
        headers={"WWW-Authenticate": challenge},
        jsonrpc_error={
            "code": AUTHENTICATION_REQUIRED_CODE,
            "message": AuthenticationRequired.message,
            "data": {"detail": reason},
        },
        title="Unauthorized",
        error="unauthorized",
    )


def _unavailable(path: str, reason: str) -> JSONResponse:
    """A ``503``: the token could not be checked yet, so the request is neither refused nor served."""
    logger.warning("Could not verify a request: %s", reason)
    internal = InternalError()
    return _refusal(
        path,
        503,
        reason,
        headers={"Retry-After": "1"},
        jsonrpc_error={"code": internal.code, "message": internal.message, "data": {"detail": reason, "retryable": True}},
        title="Service Unavailable",
        error="unavailable",
    )


def _refusal(
    path: str,
    status: int,
    reason: str,
    *,
    headers: dict[str, str],
    jsonrpc_error: dict[str, Any],
    title: str,
    error: str,
) -> JSONResponse:
    """The refusal's body in the error format of the transport ``path`` belongs to."""
    transport = transport_of(path)
    if transport is Transport.JSONRPC:
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": None, "error": jsonrpc_error}
        return JSONResponse(body, status_code=status, headers=headers)
    if transport is Transport.HTTP_JSON:
        body = {"title": title, "status": status, "detail": reason}
        return JSONResponse(body, status_code=status, headers=headers, media_type=_PROBLEM_JSON)
    return JSONResponse({"error": error, "detail": reason}, status_code=status, headers=headers)
