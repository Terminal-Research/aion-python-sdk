"""Who may reach an agent, and who the caller is, as ``AionAuthMiddleware`` decides.

The middlewares run in the order ``AppFactory`` installs them, on their own or
behind an application's ``AuthenticationMiddleware``, and the probe endpoint
reads the result the way a2a-sdk's dispatcher does: the owner, whether the
user is authenticated, and the credentials that came with it.
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware

from tests.unit.support.distribution import distribution_metadata
from tests.unit.support.request_path import ELSEWHERE, Probe, send_message
from tests.support.aion_tokens import subject
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
    assert response.json()["error"] == "unauthorized"
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
    assert response.json()["error"] == "unavailable"
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
