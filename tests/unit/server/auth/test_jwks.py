"""How ``JwksKeySource`` keeps the control plane's keys: the cache policy, row by row."""

import asyncio
import logging

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from aion.server.auth.jwks import REFRESH_AFTER_SECONDS, UNKNOWN_KID_REFETCH_INTERVAL_SECONDS
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
    clock.advance(UNKNOWN_KID_REFETCH_INTERVAL_SECONDS)
    control_plane.failing = True
    assert await source.key_for(SESSION_KID) is None


async def test_keys_load_later_once_the_control_plane_answers() -> None:
    control_plane, clock = ControlPlane(), Clock()
    control_plane.failing = True
    source = _source(control_plane, clock)
    await source.load()

    control_plane.failing = False
    clock.advance(UNKNOWN_KID_REFETCH_INTERVAL_SECONDS)

    assert await source.key_for(SESSION_KID) is not None


async def test_a_set_older_than_ten_minutes_is_refreshed_in_the_background() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.document = jwks_document(**{SESSION_KID: SESSION_KEY.public_key(), "new": ROTATED_KEY.public_key()})

    clock.advance(REFRESH_AFTER_SECONDS + 1)
    current = await source.key_for(SESSION_KID)

    assert current is not None
    assert control_plane.requests == 1  # the request in hand is verified with what is held
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert control_plane.requests == 2
    assert await source.key_for("new") is not None


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

    clock.advance(UNKNOWN_KID_REFETCH_INTERVAL_SECONDS)

    assert await source.key_for("new") is not None
    assert control_plane.requests == 2


async def test_an_unknown_kid_fetches_at_most_once_in_thirty_seconds() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()

    clock.advance(UNKNOWN_KID_REFETCH_INTERVAL_SECONDS - 1)
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


async def test_a_failed_refresh_keeps_the_keys_and_says_so(caplog) -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.failing = True

    clock.advance(REFRESH_AFTER_SECONDS + 1)
    with caplog.at_level(logging.WARNING):
        assert await source.key_for(SESSION_KID) is not None
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert control_plane.requests == 2
    assert "keeping the keys already held" in caplog.text
    # Keys are not discarded by age: the old set still verifies what it signed.
    clock.advance(10 * REFRESH_AFTER_SECONDS)
    assert await source.key_for(SESSION_KID) is not None


async def test_a_document_without_usable_keys_keeps_the_old_set() -> None:
    control_plane, clock = ControlPlane(), Clock()
    source = _source(control_plane, clock)
    await source.load()
    control_plane.document = {"keys": []}

    clock.advance(UNKNOWN_KID_REFETCH_INTERVAL_SECONDS)
    assert await source.key_for("other") is None

    assert await source.key_for(SESSION_KID) is not None


@pytest.mark.parametrize("document", [{}, {"keys": "nope"}, []], ids=["empty", "bad-keys", "not-an-object"])
async def test_a_malformed_document_is_a_failed_fetch(document) -> None:
    control_plane, clock = ControlPlane(document), Clock()
    source = _source(control_plane, clock)

    await source.load()

    assert await source.key_for(SESSION_KID) is None
