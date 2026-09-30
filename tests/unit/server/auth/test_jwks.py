"""How ``JwksKeySource`` keeps the control plane's keys: the cache policy, row by row."""

import asyncio
import logging

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from aion.server.auth.jwks import (
    MAX_KEY_SET_AGE_SECONDS,
    MISSED_REFRESHES_ALLOWED,
    REFRESH_AFTER_SECONDS,
    REFETCH_INTERVAL_SECONDS,
)
from tests.unit.support.tokens import (
    SESSION_KEY,
    SESSION_KID,
    Clock,
    ControlPlane,
    jwks_document,
)

ROTATED_KEY = ec.generate_private_key(ec.SECP256R1())


def _source(control_plane: ControlPlane, clock: Clock):
    return control_plane.key_source(clock=clock)


def test_the_refresh_age_exceeds_the_refetch_interval() -> None:
    assert MAX_KEY_SET_AGE_SECONDS == MISSED_REFRESHES_ALLOWED * REFRESH_AFTER_SECONDS
    assert MAX_KEY_SET_AGE_SECONDS > REFRESH_AFTER_SECONDS > REFETCH_INTERVAL_SECONDS


async def test_the_keys_are_loaded_at_startup() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)

    await source.load()

    assert control_plane.requests == 1
    assert await source.key_for(SESSION_KID) is not None
    assert control_plane.requests == 1


async def test_a_failed_first_load_does_not_stop_the_server_and_refuses_tokens(caplog) -> None:
    control_plane, clock = ControlPlane(), Clock()
    control_plane.failing = True
    source = _source(control_plane, clock)

    with caplog.at_level(logging.WARNING):
        await source.load()

    assert "Could not fetch the Aion verification keys" in caplog.text
    clock.advance(REFETCH_INTERVAL_SECONDS)
    control_plane.failing = True
    assert await source.key_for(SESSION_KID) is None


async def test_keys_load_later_once_the_control_plane_answers() -> None:
    control_plane, clock = ControlPlane(), Clock()
    control_plane.failing = True
    source = _source(control_plane, clock)
    await source.load()

    control_plane.failing = False
    clock.advance(REFETCH_INTERVAL_SECONDS)

    assert await source.key_for(SESSION_KID) is not None


async def test_a_set_older_than_one_minute_is_refreshed_in_the_background() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.document = jwks_document(**{"new": ROTATED_KEY.public_key()})

    clock.advance(REFRESH_AFTER_SECONDS + 1)
    current = await source.key_for(SESSION_KID)

    assert current is not None
    assert control_plane.requests == 1  # the request in hand is verified with what is held
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert control_plane.requests == 2
    assert await source.key_for("new") is not None
    assert await source.key_for(SESSION_KID) is None


async def test_a_fresh_set_is_not_refreshed() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()

    clock.advance(REFRESH_AFTER_SECONDS - 1)
    await source.key_for(SESSION_KID)
    await asyncio.sleep(0)

    assert control_plane.requests == 1


async def test_one_background_refresh_runs_at_a_time() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()

    clock.advance(REFRESH_AFTER_SECONDS + 1)
    await asyncio.gather(*(source.key_for(SESSION_KID) for _ in range(10)))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert control_plane.requests == 2


async def test_an_unknown_kid_fetches_at_once_and_finds_a_rotated_key() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.document = jwks_document(**{"new": ROTATED_KEY.public_key()})

    clock.advance(REFETCH_INTERVAL_SECONDS)

    assert await source.key_for("new") is not None
    assert control_plane.requests == 2


async def test_an_unknown_kid_fetches_at_most_once_per_refetch_interval() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()

    clock.advance(REFETCH_INTERVAL_SECONDS - 1)
    assert await source.key_for("forged-1") is None
    assert await source.key_for("forged-2") is None
    assert control_plane.requests == 1

    clock.advance(1)
    assert await source.key_for("forged-3") is None
    assert control_plane.requests == 2
    assert await source.key_for("forged-4") is None
    assert control_plane.requests == 2


async def test_concurrent_unknown_kids_share_one_fetch() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)

    results = await asyncio.gather(*(source.key_for(SESSION_KID) for _ in range(10)))

    assert all(key is not None for key in results)
    assert control_plane.requests == 1


@pytest.mark.parametrize("age_offset", [-1, 0], ids=["below-maximum-age", "at-maximum-age"])
async def test_a_failed_refresh_keeps_the_keys_within_the_maximum_age(caplog, age_offset) -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.failing = True

    clock.advance(MAX_KEY_SET_AGE_SECONDS + age_offset)
    with caplog.at_level(logging.WARNING):
        assert await source.key_for(SESSION_KID) is not None
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert control_plane.requests == 2
    assert "keeping the keys already held" in caplog.text
    assert await source.key_for(SESSION_KID) is not None
    assert source._keys
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


async def test_a_document_without_usable_keys_keeps_the_old_set() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.document = {"keys": []}

    clock.advance(REFETCH_INTERVAL_SECONDS)
    assert await source.key_for("other") is None

    assert await source.key_for(SESSION_KID) is not None


@pytest.mark.parametrize("document", [{}, {"keys": "nope"}, []], ids=["empty", "bad-keys", "not-an-object"])
async def test_a_malformed_document_is_a_failed_fetch(document) -> None:
    control_plane, clock = ControlPlane(document), Clock()
    source = _source(control_plane, clock)

    await source.load()

    assert await source.key_for(SESSION_KID) is None


async def test_a_failing_background_refresh_is_retried_at_most_once_per_refetch_interval(caplog) -> None:
    """A failed refresh leaves the set stale while the fetch rate limit bounds retries."""
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    loaded_at = source._fetched_at
    control_plane.failing = True
    clock.advance(REFRESH_AFTER_SECONDS + 1)
    assert source._is_stale()

    async def requests(count: int) -> None:
        for _ in range(count):
            assert await source.key_for(SESSION_KID) is not None
            await asyncio.sleep(0)
        await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING):
        await requests(50)
        clock.advance(REFETCH_INTERVAL_SECONDS - 1)
        assert source._is_stale()
        await requests(50)
    assert control_plane.requests == 2
    assert caplog.text.count("Could not fetch") == 1

    clock.advance(1)
    await requests(50)
    assert control_plane.requests == 3

    control_plane.failing = False
    clock.advance(REFETCH_INTERVAL_SECONDS)
    await requests(1)
    assert control_plane.requests == 4
    assert source._fetched_at > loaded_at
    assert not source._is_stale()


@pytest.mark.parametrize("background_refresh", [False, True], ids=["idle-server", "refresh-in-flight"])
async def test_an_overage_set_waits_for_one_fetch_and_uses_the_new_keys(monkeypatch, caplog, background_refresh) -> None:
    """Requests wait for the control plane before trusting a set beyond its maximum age."""
    control_plane, clock = ControlPlane(), Clock()
    started, release = asyncio.Event(), asyncio.Event()
    handle = control_plane.handle

    async def delayed_response(request):
        response = handle(request)
        if control_plane.requests > 1:
            started.set()
            await release.wait()
        return response

    monkeypatch.setattr(control_plane, "handle", delayed_response)
    source = _source(control_plane, clock)
    await source.load()
    control_plane.document = jwks_document(**{SESSION_KID: ROTATED_KEY.public_key()})

    if background_refresh:
        clock.advance(REFRESH_AFTER_SECONDS + 1)
        assert await source.key_for(SESSION_KID) is not None
        await started.wait()
        clock.advance(MAX_KEY_SET_AGE_SECONDS - REFRESH_AFTER_SECONDS)
    else:
        clock.advance(MAX_KEY_SET_AGE_SECONDS + 1)

    pending = [asyncio.create_task(source.key_for(SESSION_KID)) for _ in range(10)]
    try:
        await started.wait()
        await asyncio.sleep(0)
        assert all(not request.done() for request in pending)
        assert control_plane.requests == 2
    finally:
        release.set()
        keys = await asyncio.gather(*pending)

    assert all(key.key.public_numbers() == ROTATED_KEY.public_key().public_numbers() for key in keys)
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


async def test_an_overage_set_is_cleared_once_and_failed_fetches_are_rate_limited(caplog) -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    loaded_at = source._fetched_at
    control_plane.failing = True
    clock.advance(MAX_KEY_SET_AGE_SECONDS + 1)

    async def refused_requests() -> None:
        assert await asyncio.gather(*(source.key_for(SESSION_KID) for _ in range(50))) == [None] * 50

    with caplog.at_level(logging.ERROR, logger="aion.server.auth.jwks"):
        await refused_requests()
        assert source._keys == {}
        assert source._fetched_at == loaded_at
        assert control_plane.requests == 2

        clock.advance(REFETCH_INTERVAL_SECONDS - 1)
        await refused_requests()
        assert control_plane.requests == 2

        clock.advance(1)
        await refused_requests()
        assert control_plane.requests == 3

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert f"not been refreshed for more than {MAX_KEY_SET_AGE_SECONDS} seconds" in errors[0].message
    assert "anonymous session tokens are refused until contact with the control plane is restored" in errors[0].message


async def test_an_overage_set_is_cleared_when_the_refetch_interval_forbids_a_fetch(caplog) -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.failing = True
    clock.advance(MAX_KEY_SET_AGE_SECONDS)
    assert await source.key_for(SESSION_KID) is not None
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert control_plane.requests == 2

    clock.advance(1)
    with caplog.at_level(logging.ERROR, logger="aion.server.auth.jwks"):
        assert await source.key_for(SESSION_KID) is None
        assert await source.key_for("unknown") is None

    assert source._keys == {}
    assert control_plane.requests == 2
    assert len([record for record in caplog.records if record.levelno == logging.ERROR]) == 1


async def test_a_successful_fetch_restores_verification_and_logs_once(caplog) -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.failing = True
    clock.advance(MAX_KEY_SET_AGE_SECONDS + 1)

    with caplog.at_level(logging.INFO, logger="aion.server.auth.jwks"):
        assert await source.key_for(SESSION_KID) is None
        control_plane.failing = False
        clock.advance(REFETCH_INTERVAL_SECONDS - 1)
        assert await source.key_for(SESSION_KID) is None
        assert control_plane.requests == 2
        assert not any(record.levelno == logging.INFO for record in caplog.records)

        clock.advance(1)
        assert await source.key_for(SESSION_KID) is not None
        assert control_plane.requests == 3
        assert source._fetched_at == clock()
        clock.advance(REFRESH_AFTER_SECONDS + 1)
        assert await source.key_for(SESSION_KID) is not None
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert control_plane.requests == 4

    infos = [record for record in caplog.records if record.levelno == logging.INFO]
    assert [record.message for record in infos] == [
        "Authentication: contact with the control plane is restored; anonymous session token verification has resumed"
    ]
