"""Who may reach an agent, and who the caller is, as ``AionAuthMiddleware`` decides.

The middlewares run in the order ``AppFactory`` installs them, on their own or
behind an application's ``AuthenticationMiddleware``, and the probe endpoint
reads the result the way a2a-sdk's dispatcher does: the owner, whether the
user is authenticated, and the credentials that came with it.
"""

import asyncio
import base64
import datetime
import json
import time

import httpx
import jwt
import pytest
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser, UnauthenticatedUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route

from aion.server.auth.verifier import CLOCK_SKEW_LEEWAY_SECONDS, INVOCATION_LIFETIME_SECONDS as INVOCATION_LIFETIME
from aion.server.core.errors import AUTHENTICATION_REQUIRED_CODE
from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware

from tests.unit.support.distribution import distribution_metadata
from tests.unit.support.request_path import ELSEWHERE, Probe, send_message
from tests.support.aion_tokens import SigningKey, subject
from tests.unit.support.tokens import AION_KEY, ControlPlane, hosted_verifier, verifier

USER_ID = "7a9e2b1c-1111-4d2e-8f3a-6b5c4d3e2f10"
SESSION_ID = "f9d7daea-df95-4111-8533-5d6f043edaf9"
PLATFORM_SUBJECT = subject("AionUser", USER_ID)
SESSION_SUBJECT = subject("AnonymousSession", SESSION_ID)


def platform_token(**claims) -> str:
    return AION_KEY.invocation_token(USER_ID, **claims)


def session_token() -> str:
    return AION_KEY.session_token(SESSION_ID)


class _Alice(AuthenticationBackend):
    """An application's own authentication: every request is alice's, fully authenticated."""

    async def authenticate(self, conn):
        return AuthCredentials(["authenticated"]), SimpleUser("alice")


def _aion() -> list[Middleware]:
    return [Middleware(AionAuthMiddleware, verifier=hosted_verifier()), Middleware(AionContextMiddleware)]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _rpc_reason(response: httpx.Response) -> str:
    """The reason a refusal on the JSON-RPC endpoint gives."""
    return response.json()["error"]["data"]["detail"]


async def test_the_token_names_the_owner() -> None:
    probe = Probe()
    async with probe.client(*_aion()) as client:
        answer = (await send_message(client, headers=_bearer(platform_token()))).json()

    assert (answer["owner"], answer["authenticated"], answer["scopes"]) == (
        PLATFORM_SUBJECT,
        True,
        ["authenticated"],
    )


async def test_an_anonymous_session_token_names_the_owner_too() -> None:
    probe = Probe()
    middleware = [Middleware(AionAuthMiddleware, verifier=verifier()), Middleware(AionContextMiddleware)]
    async with probe.client(*middleware) as client:
        answer = (await send_message(client, headers=_bearer(session_token()))).json()

    assert (answer["owner"], answer["authenticated"]) == (SESSION_SUBJECT, True)


async def test_a_hosted_server_refuses_an_anonymous_session_token() -> None:
    probe = Probe()
    async with probe.client(*_aion()) as client:
        response = await send_message(client, headers=_bearer(session_token()))

    assert response.status_code == 401
    assert probe.calls == 0


async def test_a_distribution_is_channel_context_not_the_owner() -> None:
    """The distribution still reaches the execution scope; the owner is the token's ``sub``."""
    probe = Probe()
    async with probe.client(*_aion()) as client:
        answer = (
            await send_message(client, distribution_metadata("dist-1"), headers=_bearer(platform_token()))
        ).json()

    assert (answer["owner"], answer["scope_distribution"]) == (PLATFORM_SUBJECT, "dist-1")


async def test_the_token_replaces_a_user_an_earlier_middleware_set() -> None:
    probe = Probe()
    async with probe.client(Middleware(AuthenticationMiddleware, backend=_Alice()), *_aion()) as client:
        answer = (await send_message(client, headers=_bearer(platform_token()))).json()

    assert answer["owner"] == PLATFORM_SUBJECT


async def test_other_headers_name_nobody() -> None:
    """Only the verified token names the caller.

    Also pins the SDK to a2a-sdk's builder: the owner comes from
    ``request.scope["user"]`` and nowhere else. Should the builder start
    taking a user from another part of the request, this fails first.
    """
    probe = Probe()
    async with probe.client(*_aion()) as client:
        answer = (
            await send_message(
                client,
                headers={**_bearer(platform_token()), "X-User": "mallory", "X-Test-User": "mallory"},
            )
        ).json()

    assert answer["owner"] == PLATFORM_SUBJECT


@pytest.mark.parametrize(
    ("headers", "challenge"),
    [
        ({}, "Bearer"),
        ({"Authorization": "Basic YWxpY2U6c2VjcmV0"}, "Bearer"),
        ({"Authorization": "Bearer "}, "Bearer"),
        (_bearer(platform_token(issued_at=0)), 'Bearer error="invalid_token"'),
        (_bearer("not-a-jwt"), 'Bearer error="invalid_token"'),
    ],
    ids=["no-header", "basic", "empty-bearer", "expired", "garbage"],
)
async def test_a_request_without_a_valid_token_never_reaches_the_agent(headers, challenge) -> None:
    probe = Probe()
    async with probe.client(*_aion()) as client:
        response = await send_message(client, distribution_metadata("dist-1"), headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge
    assert response.json()["error"]["code"] == AUTHENTICATION_REQUIRED_CODE
    assert probe.calls == 0


async def test_a_refusal_never_quotes_the_token(caplog) -> None:
    token = platform_token(issued_at=0)

    probe = Probe()
    async with probe.client(*_aion()) as client:
        response = await send_message(client, headers=_bearer(token))

    assert token not in response.text
    assert token not in caplog.text


async def test_until_the_keys_load_a_request_is_answered_503_and_never_served() -> None:
    """An outage is neither a pass nor a verdict on the token: the client may retry."""
    control_plane = ControlPlane()
    control_plane.failing = True
    probe = Probe()
    middleware = [Middleware(AionAuthMiddleware, verifier=hosted_verifier(control_plane)), Middleware(AionContextMiddleware)]
    async with probe.client(*middleware) as client:
        response = await send_message(client, headers=_bearer(platform_token()))

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["data"]["retryable"] is True
    assert probe.calls == 0


async def test_every_other_route_needs_a_token_too() -> None:
    """Closed by default: a route an application adds is not open because it is not the JSON-RPC one."""
    probe = Probe()
    async with probe.client(*_aion()) as client:
        refused = await send_message(client, path=ELSEWHERE)
        let_in = await send_message(client, path=ELSEWHERE, headers=_bearer(platform_token()))

    assert refused.status_code == 401
    assert let_in.status_code == 200
    assert let_in.json() == {"user": PLATFORM_SUBJECT, "scopes": ["authenticated"]}


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/agent-card.json",
        "/health/",
        "/health",
        "/.well-known/configuration.json",
    ],
)
async def test_the_public_paths_need_no_token(path) -> None:
    """How to call the agent, whether it is up, and its configuration schema are read before any token."""
    app = Starlette(
        routes=[Route(path, lambda request: PlainTextResponse("open"), methods=["GET"])],
        middleware=_aion(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)

    assert (response.status_code, response.text) == (200, "open")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/.well-known/agent-card.json/extra"])
async def test_nothing_else_is_public(path) -> None:
    app = Starlette(
        routes=[Route(path, lambda request: PlainTextResponse("open"), methods=["GET"])],
        middleware=_aion(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 401


def _who(request) -> PlainTextResponse:
    """The caller this middleware installed, if any."""
    user = request.scope.get("user")
    return PlainTextResponse(type(user).__name__ if user is not None else "nobody")


async def _get(app: Starlette, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(kwargs.pop("method", "GET"), path, **kwargs)


def _with_application_routes(sdk: list[Route], application: list[Route]) -> Starlette:
    """The server's own routes first, then the application's, which the middleware leaves to the application."""
    return Starlette(
        routes=[*sdk, *application],
        middleware=[Middleware(AionAuthMiddleware, verifier=hosted_verifier(), application_routes=application)],
    )


@pytest.mark.parametrize(
    "headers", [None, _bearer("not-a-token"), _bearer("x" * 9000)], ids=["no-token", "foreign", "oversized"]
)
async def test_the_applications_routes_are_left_to_the_application(headers) -> None:
    """No token is asked for, read or refused there, and no caller is installed: who may call them is the app's."""
    app = _with_application_routes(
        sdk=[Route("/", lambda request: PlainTextResponse("agent"), methods=["POST"])],
        application=[Route("/api/items/{item_id}", _who, methods=["GET"])],
    )

    response = await _get(app, "/api/items/42", headers=headers)

    assert (response.status_code, response.text) == (200, "nobody")
    assert (await _get(app, "/", method="POST", headers=headers)).status_code == 401


async def test_an_application_route_answering_another_method_is_still_the_applications() -> None:
    app = _with_application_routes(sdk=[], application=[Route("/api/items", _who, methods=["GET"])])

    assert (await _get(app, "/api/items", method="POST")).status_code == 405


async def test_an_application_route_never_opens_a_path_the_server_answers_itself() -> None:
    """Starlette dispatches to the first match, so the server's route shadows an application route on its path."""
    app = _with_application_routes(
        sdk=[Route("/shared", lambda request: PlainTextResponse("server"), methods=["GET"])],
        application=[Route("/shared", _who, methods=["GET"]), Route("/{anything:path}", _who, methods=["GET"])],
    )

    assert (await _get(app, "/shared")).status_code == 401
    assert (await _get(app, "/elsewhere")).text == "nobody"


class _Anonymous(AuthenticationBackend):
    """An application's authentication that installs a user without vouching for it."""

    async def authenticate(self, conn):
        return AuthCredentials([]), UnauthenticatedUser()


class _Nameless(AuthenticationBackend):
    """An application's authentication whose user has no name to own anything by."""

    async def authenticate(self, conn):
        return AuthCredentials(["authenticated"]), SimpleUser("  ")


def _remote(backend: AuthenticationBackend | None = _Alice(), *, verifier_=None) -> list[Middleware]:
    """The middlewares of a server outside the platform, behind the application's own authentication."""
    application = [Middleware(AuthenticationMiddleware, backend=backend)] if backend is not None else []
    return [
        *application,
        Middleware(AionAuthMiddleware, verifier=verifier_ or verifier()),
        Middleware(AionContextMiddleware),
    ]


def _application_jwt() -> str:
    return jwt.encode({"sub": "alice"}, "an-application-secret-long-enough-for-hs256", algorithm="HS256")


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer an-opaque-application-key"}, {"Authorization": f"Bearer {_application_jwt()}"}],
    ids=["no-token", "opaque-token", "application-jwt"],
)
async def test_outside_the_platform_the_applications_user_is_served_without_an_aion_token(headers) -> None:
    probe = Probe()
    async with probe.client(*_remote()) as client:
        answer = (await send_message(client, headers=headers)).json()

    assert (answer["owner"], answer["authenticated"]) == ("alice", True)


@pytest.mark.parametrize(
    "token",
    [
        lambda: platform_token(issued_at=0),
        lambda: platform_token(audience="another-client"),
        lambda: SigningKey().invocation_token(USER_ID),
    ],
    ids=["expired", "other-audience", "unknown-key"],
)
async def test_an_aion_token_that_does_not_verify_never_falls_back_to_the_applications_user(token) -> None:
    probe = Probe()
    async with probe.client(*_remote()) as client:
        response = await send_message(client, headers=_bearer(token()))

    assert response.status_code == 401
    assert probe.calls == 0


@pytest.mark.parametrize(
    "token",
    [
        lambda: AION_KEY.invocation_token(USER_ID, headers={"typ": None}),
        lambda: AION_KEY.session_token(SESSION_ID, headers={"typ": "JWT"}),
    ],
    ids=["no-typ", "jwt-typ"],
)
async def test_a_damaged_aion_token_is_refused_even_with_an_application_user(token) -> None:
    probe = Probe()
    async with probe.client(*_remote()) as client:
        response = await send_message(client, headers=_bearer(token()))

    assert response.status_code == 401
    assert _rpc_reason(response) == "the bearer token is a damaged Aion token"
    assert probe.calls == 0


@pytest.mark.parametrize(
    "token",
    [
        lambda: jwt.encode({"sub": "alice", "padding": "x" * 8192}, "an-application-secret-long-enough-for-hs256",
                           algorithm="HS256"),
        lambda: "k" * 8193,
        lambda: AION_KEY.invocation_token(USER_ID, headers={"typ": None}, padding="x" * 8192),
        lambda: platform_token(padding="x" * 8192),
    ],
    ids=["application-jwt", "opaque-application-key", "aion-token-use-without-typ", "aion-token"],
)
async def test_a_bearer_over_8_kib_is_refused_even_with_an_application_user(token) -> None:
    """The limit holds for every bearer token, the application's own included, and is checked before routing."""
    probe = Probe()
    async with probe.client(*_remote()) as client:
        response = await send_message(client, headers=_bearer(token()))

    assert response.status_code == 401
    assert _rpc_reason(response) == "the bearer token is longer than 8192 bytes"
    assert probe.calls == 0


async def test_a_bearer_of_exactly_8_kib_is_not_refused_for_its_length() -> None:
    probe = Probe()
    async with probe.client(*_remote()) as client:
        answer = (await send_message(client, headers=_bearer("k" * 8192))).json()

    assert answer["owner"] == "alice"


async def test_an_outage_never_falls_back_to_the_applications_user() -> None:
    control_plane = ControlPlane()
    control_plane.failing = True
    probe = Probe()
    async with probe.client(*_remote(verifier_=verifier(control_plane))) as client:
        response = await send_message(client, headers=_bearer(session_token()))

    assert response.status_code == 503
    assert probe.calls == 0


@pytest.mark.parametrize("backend", [_Anonymous(), _Nameless(), None], ids=["unauthenticated", "nameless", "none"])
@pytest.mark.parametrize(
    ("headers", "challenge"),
    [({}, "Bearer"), ({"Authorization": "Bearer an-opaque-application-key"}, 'Bearer error="invalid_token"')],
    ids=["no-token", "foreign-token"],
)
async def test_without_an_application_user_to_serve_the_request_is_refused(backend, headers, challenge) -> None:
    probe = Probe()
    async with probe.client(*_remote(backend)) as client:
        response = await send_message(client, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge
    assert probe.calls == 0


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer an-opaque-application-key"}],
    ids=["no-token", "foreign-token"],
)
async def test_a_hosted_or_strict_server_serves_no_application_user(headers) -> None:
    probe = Probe()
    async with probe.client(*_remote(verifier_=hosted_verifier())) as client:
        response = await send_message(client, headers=headers)

    assert response.status_code == 401
    assert probe.calls == 0


async def test_a_hosted_or_strict_server_still_serves_a_verified_invocation() -> None:
    probe = Probe()
    async with probe.client(*_remote(verifier_=hosted_verifier())) as client:
        answer = (await send_message(client, headers=_bearer(platform_token()))).json()

    assert answer["owner"] == PLATFORM_SUBJECT


@pytest.mark.parametrize("path", [None, ELSEWHERE], ids=["json-rpc", "application-route"])
async def test_a_hosted_server_refuses_a_session_on_every_protected_route(path) -> None:
    probe = Probe()
    async with probe.client(*_aion()) as client:
        kwargs = {"path": path} if path else {}
        response = await send_message(client, headers=_bearer(session_token()), **kwargs)

    assert response.status_code == 401
    assert probe.calls == 0


async def test_a_stream_outlives_its_tokens_expiry_and_the_next_request_is_refused(monkeypatch) -> None:
    """One token: the stream it opened keeps going past ``exp`` + leeway; the same token is refused after."""
    issued_at = int(time.time())
    monkeypatch.setattr(_JwtClock, "moment", issued_at)
    monkeypatch.setattr(jwt.api_jwt, "datetime", _JwtClock)
    token = platform_token(issued_at=issued_at)

    async def events():
        yield "data: 0\n\n"
        _JwtClock.moment = issued_at + INVOCATION_LIFETIME + CLOCK_SKEW_LEEWAY_SECONDS + 1
        for index in (1, 2):
            await asyncio.sleep(0)
            yield f"data: {index}\n\n"

    app = Starlette(
        routes=[Route("/stream", lambda request: StreamingResponse(events(), media_type="text/event-stream"))],
        middleware=[Middleware(AionAuthMiddleware, verifier=hosted_verifier())],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        streamed = await client.get("/stream", headers=_bearer(token))
        assert streamed.status_code == 200
        assert streamed.text.count("data:") == 3

        again = await client.get("/stream", headers=_bearer(token))
        assert again.status_code == 401
        assert "expired" in again.json()["detail"]


class _JwtClock(datetime.datetime):
    """PyJWT's ``datetime``, reading the time the test sets in ``moment``."""

    moment: float = 0.0

    @classmethod
    def now(cls, tz=None):
        return datetime.datetime.fromtimestamp(cls.moment, tz=tz)


@pytest.mark.parametrize(
    "token",
    [
        lambda: _unsigned({"alg": "HS256"}, {"token_use": []}),
        lambda: _unsigned({"alg": "HS256"}, {"token_use": {}}),
        lambda: _unsigned({"alg": "ES256", "typ": ["aion-invocation+jwt"]}, {}),
        lambda: _unsigned({"alg": "ES256", "typ": "aion-invocation+jwt", "kid": AION_KEY.kid}, {"iat": []}),
        lambda: _signed_claims(iat=[]),
        lambda: _signed_claims(nbf={}),
        lambda: _signed_claims(exp=[]),
        lambda: _signed_claims(iss=[]),
        lambda: _signed_claims(aud={}),
        lambda: platform_token(padding="x" * 8192),
    ],
    ids=["token-use-list", "token-use-object", "typ-list", "unsigned-iat-list", "iat-list", "nbf-object",
         "exp-list", "iss-list", "aud-object", "oversized"],
)
async def test_a_malformed_bearer_is_refused_with_401_never_a_server_error(token) -> None:
    probe = Probe()
    async with probe.client(*_aion()) as client:
        response = await send_message(client, headers=_bearer(token()))

    assert response.status_code == 401
    assert probe.calls == 0


def _unsigned(header: dict, claims: dict) -> str:
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return f"{encode(header)}.{encode(claims)}.c2lnbmF0dXJl"


def _signed_claims(**replaced) -> str:
    claims = jwt.decode(platform_token(), options={"verify_signature": False})
    return AION_KEY.sign_raw(AION_KEY.header_json(), json.dumps({**claims, **replaced}))


# A refusal answers in the error format of the transport its path belongs to.

CONTEXT_GET = "/context:get"
OPENAPI = "/openapi.json"

_REFUSALS = {
    "no-token": ({}, "Bearer", "the request carries no bearer token"),
    "invalid-token": (
        _bearer(platform_token(issued_at=0)),
        'Bearer error="invalid_token"',
        None,
    ),
    "damaged-aion-token": (
        _bearer(AION_KEY.invocation_token(USER_ID, headers={"typ": None})),
        'Bearer error="invalid_token"',
        "the bearer token is a damaged Aion token",
    ),
    "over-8-kib": (_bearer("k" * 8193), 'Bearer error="invalid_token"', "the bearer token is longer than 8192 bytes"),
}


async def _refused(path: str, headers: dict[str, str], middleware: list[Middleware] | None = None) -> httpx.Response:
    probe = Probe()
    async with probe.client(*(middleware or _aion())) as client:
        response = await client.post(path, json={"jsonrpc": "2.0", "id": "req-1", "method": "GetContexts"},
                                     headers=headers)
    assert probe.calls == 0
    return response


@pytest.mark.parametrize(("headers", "challenge", "reason"), _REFUSALS.values(), ids=_REFUSALS.keys())
async def test_the_jsonrpc_endpoint_refuses_with_a_jsonrpc_error(headers, challenge, reason) -> None:
    """Aion's ``-32051``, answering ``id`` ``null``: the body is never read."""
    response = await _refused("/", headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["jsonrpc"] == "2.0" and body["id"] is None
    assert (body["error"]["code"], body["error"]["message"]) == (-32051, "Unauthorized")
    assert set(body["error"]["data"]) == {"detail"}
    if reason is not None:
        assert body["error"]["data"]["detail"] == reason


@pytest.mark.parametrize(("headers", "challenge", "reason"), _REFUSALS.values(), ids=_REFUSALS.keys())
@pytest.mark.parametrize("path", ["/contexts:get", CONTEXT_GET, "/context:delete", f"{CONTEXT_GET}/"])
async def test_a_context_http_route_refuses_with_a_problem_detail(path, headers, challenge, reason) -> None:
    """No ``type``: the specification defines none for a missing authentication."""
    response = await _refused(path, headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert set(body) == {"title", "status", "detail"}
    assert (body["title"], body["status"]) == ("Unauthorized", 401)
    if reason is not None:
        assert body["detail"] == reason


@pytest.mark.parametrize(("headers", "challenge", "reason"), _REFUSALS.values(), ids=_REFUSALS.keys())
@pytest.mark.parametrize("path", [OPENAPI, ELSEWHERE, "/context:get/extra"])
async def test_any_other_closed_path_refuses_with_a_plain_error(path, headers, challenge, reason) -> None:
    response = await _refused(path, headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge
    body = response.json()
    assert set(body) == {"error", "detail"}
    assert body["error"] == "unauthorized"
    if reason is not None:
        assert body["detail"] == reason


def _outage() -> list[Middleware]:
    control_plane = ControlPlane()
    control_plane.failing = True
    return [Middleware(AionAuthMiddleware, verifier=hosted_verifier(control_plane)), Middleware(AionContextMiddleware)]


async def test_an_outage_on_the_jsonrpc_endpoint_is_a_retryable_internal_error() -> None:
    response = await _refused("/", _bearer(platform_token()), _outage())

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    body = response.json()
    assert body["id"] is None
    assert body["error"]["code"] == -32603
    assert body["error"]["data"]["retryable"] is True
    assert body["error"]["data"]["detail"]


async def test_an_outage_on_a_context_http_route_is_a_503_problem_detail() -> None:
    response = await _refused(CONTEXT_GET, _bearer(platform_token()), _outage())

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["title"], body["status"]) == ("Service Unavailable", 503)
    assert body["detail"]


async def test_an_outage_elsewhere_is_a_plain_error() -> None:
    response = await _refused(OPENAPI, _bearer(platform_token()), _outage())

    assert response.status_code == 503
    assert response.json()["error"] == "unavailable"
