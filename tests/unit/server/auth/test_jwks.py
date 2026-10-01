"""How ``JwksKeySource`` gets Aion's keys and keeps them: once loaded, for the life of the process."""

import asyncio
import logging

import httpx
import pytest

from aion.server.auth.jwks import COLD_RETRY_SECONDS, MAX_DOCUMENT_BYTES, JwksKeySource
from tests.support.aion_tokens import SigningKey
from tests.unit.support.tokens import AION_KEY, JWKS_URL, Clock, ControlPlane


async def test_the_keys_load_at_startup() -> None:
    control_plane = ControlPlane()
    source = control_plane.key_source()

    await source.load()

    assert source.available
    assert (await source.key_for(AION_KEY.kid)) is not None
    assert control_plane.requests == 1


async def test_loaded_keys_are_kept_without_another_fetch_however_long_it_runs() -> None:
    """No timer and no refresh: an outage after startup changes nothing."""
    clock = Clock()
    control_plane = ControlPlane()
    source = control_plane.key_source(clock)
    await source.load()

    control_plane.failing = True
    clock.advance(30 * 24 * 3600)

    assert (await source.key_for(AION_KEY.kid)) is not None
    assert control_plane.requests == 1


async def test_an_unknown_kid_is_refused_without_a_fetch_once_keys_are_loaded() -> None:
    clock = Clock()
    control_plane = ControlPlane()
    source = control_plane.key_source(clock)
    await source.load()
    clock.advance(3600)

    assert (await source.key_for("unknown")) is None
    assert control_plane.requests == 1


async def test_a_failed_startup_fetch_is_retried_by_a_later_request_at_most_once_a_second(caplog) -> None:
    clock = Clock()
    control_plane = ControlPlane()
    control_plane.failing = True
    source = control_plane.key_source(clock)
    with caplog.at_level(logging.WARNING, logger="aion.server.auth.jwks"):
        await source.load()
    assert not source.available
    assert "protected requests are refused" in caplog.text

    assert (await source.key_for(AION_KEY.kid)) is None
    assert control_plane.requests == 1

    clock.advance(COLD_RETRY_SECONDS)
    control_plane.failing = False
    assert (await source.key_for(AION_KEY.kid)) is not None
    assert control_plane.requests == 2


async def test_a_continuing_outage_warns_once(caplog) -> None:
    clock = Clock()
    control_plane = ControlPlane()
    control_plane.failing = True
    source = control_plane.key_source(clock)

    with caplog.at_level(logging.WARNING, logger="aion.server.auth.jwks"):
        for _ in range(3):
            await source.key_for(AION_KEY.kid)
            clock.advance(COLD_RETRY_SECONDS)

    assert control_plane.requests == 3
    assert caplog.text.count("could not fetch") == 1


async def test_concurrent_cold_requests_share_one_fetch() -> None:
    release = asyncio.Event()
    requests = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        await release.wait()
        return httpx.Response(200, json=AION_KEY.jwks())

    source = JwksKeySource(JWKS_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    waiting = [asyncio.create_task(source.key_for(AION_KEY.kid)) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()

    assert all(key is not None for key in await asyncio.gather(*waiting))
    assert requests == 1


def _source_answering(response: httpx.Response) -> JwksKeySource:
    return JwksKeySource(JWKS_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://elsewhere.example/keys"}),
        httpx.Response(404),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"keys": "none"}),
        httpx.Response(200, json=[AION_KEY.public_jwk()]),
        httpx.Response(200, content=b'{"keys": [], "keys": []}'),
        httpx.Response(200, content=b'{"keys": [], "padding": "' + b"x" * MAX_DOCUMENT_BYTES + b'"}'),
    ],
    ids=["redirect", "not-found", "not-json", "keys-not-a-list", "not-an-object", "duplicate-member", "too-large"],
)
async def test_a_response_that_is_not_a_bounded_jwks_loads_nothing(response) -> None:
    source = _source_answering(response)

    await source.load()

    assert not source.available


async def test_the_fetch_has_a_deadline(monkeypatch) -> None:
    import aion.server.auth.jwks as jwks

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        return httpx.Response(200, json=AION_KEY.jwks())

    monkeypatch.setattr(jwks, "FETCH_DEADLINE_SECONDS", 0.05)
    source = JwksKeySource(JWKS_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(hang)))

    await asyncio.wait_for(source.load(), timeout=5)

    assert not source.available


@pytest.mark.parametrize(
    ("jwk", "kept"),
    [
        (AION_KEY.public_jwk(), True),
        (AION_KEY.public_jwk(alg=None, use=None), True),
        (AION_KEY.public_jwk(kid="not-the-thumbprint"), False),
        (AION_KEY.public_jwk(alg="ES384"), False),
        (AION_KEY.public_jwk(use="enc"), False),
        (AION_KEY.public_jwk(crv="P-384"), False),
        (AION_KEY.public_jwk(d="private"), False),
        ({"kty": "oct", "kid": "shared", "k": "c2VjcmV0"}, False),
    ],
    ids=["published", "no-alg-or-use", "kid-not-thumbprint", "other-alg", "encryption-key", "other-curve",
         "private-key", "symmetric"],
)
async def test_only_es256_public_keys_named_by_their_thumbprint_are_kept(jwk, kept) -> None:
    source = _source_answering(httpx.Response(200, json={"keys": [jwk]}))

    await source.load()

    assert source.available is kept


async def test_a_kid_published_twice_is_ambiguous_and_used_for_neither() -> None:
    other = SigningKey()
    source = _source_answering(
        httpx.Response(200, json={"keys": [AION_KEY.public_jwk(), AION_KEY.public_jwk(), other.public_jwk()]})
    )

    await source.load()

    assert (await source.key_for(AION_KEY.kid)) is None
    assert (await source.key_for(other.kid)) is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://api.aion.example/runtime/a2a/verification-keys",
        "ftp://api.aion.example/keys",
        "https:///keys",
    ],
)
def test_keys_come_over_https(url) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        JwksKeySource(url)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "aion.localhost"])
async def test_plain_http_is_allowed_to_this_machine_only(host) -> None:
    source = JwksKeySource(f"http://{host}:8080/runtime/a2a/verification-keys")
    await source.aclose()
