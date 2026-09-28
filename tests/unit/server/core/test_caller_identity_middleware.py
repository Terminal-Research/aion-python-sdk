"""Who the caller of an A2A request is, as ``CallerIdentityMiddleware`` names it.

The middlewares run in the order ``AppFactory`` installs them, on their own or
behind an application's ``AuthenticationMiddleware``, and the probe endpoint
reads the result the way a2a-sdk's dispatcher does: the owner, whether the
user is authenticated, and the credentials that came with it.
"""

import logging
from typing import Any

import pytest
from a2a.utils import DEFAULT_RPC_URL
from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request

from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.server.core.middlewares import AionContextMiddleware, CallerIdentityMiddleware
from aion.server.core.middlewares import _rpc_request

from tests.unit.support.distribution import distribution_metadata
from tests.unit.support.request_path import ELSEWHERE, Probe, send_message

INVALID_REQUEST = -32600
SECRET = "s3cr3t-value"


class _Alice(AuthenticationBackend):
    """An application's own authentication: every request is alice's, fully authenticated."""

    async def authenticate(self, conn):
        return AuthCredentials(["authenticated"]), SimpleUser("alice")


AION = [Middleware(CallerIdentityMiddleware), Middleware(AionContextMiddleware)]
BEHIND_AUTHENTICATION = [Middleware(AuthenticationMiddleware, backend=_Alice()), *AION]


def _malformed(distribution_id: str = "dist-1", **configuration_variables: Any) -> dict[str, Any]:
    metadata = distribution_metadata(distribution_id)
    payload = metadata[DISTRIBUTION_EXTENSION_URI_V1]
    del payload["environment"]["projectId"]
    payload["environment"]["configurationVariables"] = configuration_variables
    return metadata


@pytest.mark.parametrize(
    ("middleware", "metadata", "expected"),
    [
        (AION, distribution_metadata("dist-1"), ("dist-1", False, [])),
        (AION, None, ("", False, None)),
        (BEHIND_AUTHENTICATION, distribution_metadata("dist-1"), ("dist-1", False, [])),
        (BEHIND_AUTHENTICATION, None, ("alice", True, ["authenticated"])),
    ],
    ids=[
        "distribution",
        "anonymous",
        "distribution-behind-authentication",
        "application-user",
    ],
)
async def test_the_user_and_the_credentials_describe_one_caller(middleware, metadata, expected) -> None:
    """A distribution replaces whatever pair was there; without one the pair is left alone.

    The unauthenticated distribution never keeps the application user's
    ``authenticated`` scope, and an application's user is not touched when no
    distribution names anybody.
    """
    probe = Probe()
    async with probe.client(*middleware) as client:
        answer = (await send_message(client, metadata)).json()

    assert (answer["owner"], answer["authenticated"], answer["scopes"]) == expected
    assert answer["scope_distribution"] == ("dist-1" if metadata else None)


@pytest.mark.parametrize(
    ("metadata", "owner"),
    [(distribution_metadata("dist-1"), "dist-1"), (None, "")],
    ids=["with-distribution", "anonymous"],
)
async def test_headers_name_nobody(metadata, owner) -> None:
    """Only the payload names the caller.

    Also pins the SDK to a2a-sdk's builder: the owner comes from
    ``request.scope["user"]`` and nowhere else. Should the builder start
    taking a user from another part of the request, this fails first.
    """
    probe = Probe()
    async with probe.client(*AION) as client:
        answer = (
            await send_message(
                client,
                metadata,
                headers={"Authorization": "Bearer someone", "X-User": "mallory", "X-Test-User": "mallory"},
            )
        ).json()

    assert (answer["owner"], answer["authenticated"]) == (owner, False)


@pytest.mark.parametrize(
    ("middleware", "expected"),
    [(AION, {"user": None, "scopes": None}), (BEHIND_AUTHENTICATION, {"user": "alice", "scopes": ["authenticated"]})],
    ids=["no-authentication", "behind-authentication"],
)
async def test_requests_outside_the_endpoint_keep_their_scope(middleware, expected) -> None:
    probe = Probe()
    async with probe.client(*middleware) as client:
        response = await send_message(client, distribution_metadata("dist-1"), path=ELSEWHERE)

    assert response.json() == expected


async def test_the_resolver_given_to_the_middleware_decides_the_caller() -> None:
    """The policy is the middleware's to be given; it sees the request and the parsed payload."""
    seen: list[tuple[str, str]] = []

    async def resolve(request: Request, distribution) -> tuple[AuthCredentials, SimpleUser]:
        seen.append((request.url.path, distribution.distribution.id))
        return AuthCredentials(["verified"]), SimpleUser("verified-caller")

    probe = Probe()
    async with probe.client(
        Middleware(CallerIdentityMiddleware, resolve_caller=resolve), Middleware(AionContextMiddleware)
    ) as client:
        named = (await send_message(client, distribution_metadata("dist-1"))).json()
        anonymous = (await send_message(client)).json()

    assert (named["owner"], named["authenticated"], named["scopes"]) == ("verified-caller", True, ["verified"])
    assert (anonymous["owner"], anonymous["authenticated"], anonymous["scopes"]) == ("", False, None)
    assert seen == [(DEFAULT_RPC_URL, "dist-1")]


def _empty_id() -> dict[str, Any]:
    metadata = distribution_metadata("dist-1")
    metadata[DISTRIBUTION_EXTENSION_URI_V1]["distribution"]["id"] = ""
    return metadata


@pytest.mark.parametrize("metadata", [_malformed(), _empty_id()], ids=["missing-required-field", "empty-id"])
@pytest.mark.parametrize(
    ("request_id", "answered"),
    [({"a": 1}, None), ([1, 2], None), ("req-1", "req-1"), (7, 7)],
    ids=["object-id", "array-id", "string-id", "integer-id"],
)
async def test_a_malformed_distribution_is_refused(metadata, request_id, answered) -> None:
    """Refused as an invalid request, answered to the id a2a-sdk would answer to, never served."""
    probe = Probe()
    async with probe.client(*AION) as client:
        response = await send_message(client, metadata, request_id=request_id)

    assert response.status_code == 200
    answer = response.json()
    assert answer["error"]["code"] == INVALID_REQUEST
    assert answer["id"] == answered
    assert probe.calls == 0


async def test_a_refusal_keeps_the_payload_out_of_the_log_and_the_answer(caplog) -> None:
    """Configuration variables hold secrets in plain text; only field paths and messages are told."""
    metadata = _malformed(API_KEY=SECRET)
    metadata[DISTRIBUTION_EXTENSION_URI_V1]["distribution"]["url"] = {"token": SECRET}

    probe = Probe()
    with caplog.at_level(logging.WARNING):
        async with probe.client(*AION) as client:
            answer = (await send_message(client, metadata)).json()

    detail = answer["error"]["data"]
    assert "environment.projectId: Field required [missing]" in detail
    assert "distribution.url" in detail
    assert SECRET not in detail
    assert "invalid extension payload" in caplog.text
    assert SECRET not in caplog.text


async def test_the_middlewares_share_one_preparation(monkeypatch) -> None:
    """Among the middlewares the body is decoded and the payload validated once.

    Only among them: the request handler's extension pipeline validates the
    payload again for ``SendMessage``, which this does not cover.
    """
    prepared = 0
    prepare = _rpc_request._prepare

    async def counting(request):
        nonlocal prepared
        prepared += 1
        return await prepare(request)

    monkeypatch.setattr(_rpc_request, "_prepare", counting)

    probe = Probe()
    async with probe.client(*AION) as client:
        answer = (await send_message(client, distribution_metadata("dist-1"))).json()

    assert (answer["owner"], answer["scope_distribution"]) == ("dist-1", "dist-1")
    assert prepared == 1
