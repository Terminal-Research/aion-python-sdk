"""Which bearer tokens ``TokenVerifier`` accepts, and the caller it names from one."""

import logging
import time
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from aion.server.auth import InvalidTokenError, PlatformKeySource, TokenVerifier
from tests.unit.support.tokens import (
    PLATFORM_KEY,
    PLATFORM_SUBJECT,
    StaticKeySource,
    platform_token,
    platform_verifier,
)


def test_a_platform_token_names_its_subject_and_keeps_its_claims() -> None:
    caller = platform_verifier().verify(platform_token())

    assert (caller.subject, caller.display_name, caller.is_authenticated) == (PLATFORM_SUBJECT, PLATFORM_SUBJECT, True)
    assert caller.issuer == "https://aion.example"
    assert caller.claims["distribution_id"] == "distribution-321"
    assert caller.claims["caller_kind"] == "authenticated"


def test_the_audience_is_not_checked() -> None:
    """The platform's tokens carry ``aud``; nothing here compares it yet."""
    caller = platform_verifier().verify(platform_token(aud="aion:runtime:another-version:agent:another"))

    assert caller.subject == PLATFORM_SUBJECT


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (lambda: platform_token(lifetime=-120), "expired"),
        (lambda: platform_token(nbf=int(time.time()) + 600), "not valid yet"),
        (lambda: platform_token(None), "'sub'"),
        (lambda: platform_token(""), "'sub'"),
        (lambda: platform_token(exp=None), "'exp'"),
        (lambda: platform_token(key=ec.generate_private_key(ec.SECP256R1())), "does not verify"),
        (lambda: platform_token(key="a-shared-secret-long-enough-for-hs256", algorithm="HS256"), "does not verify"),
        (lambda: jwt.encode({"sub": PLATFORM_SUBJECT, "exp": int(time.time()) + 60}, None, algorithm="none"),
         "does not verify"),
        (lambda: "not-a-jwt", "not a JWT"),
    ],
    ids=["expired", "not-yet-valid", "no-sub", "empty-sub", "no-exp", "other-key", "hs256", "alg-none", "garbage"],
)
def test_a_token_that_does_not_verify_is_refused(token, reason) -> None:
    raw = token()

    with pytest.raises(InvalidTokenError, match=reason) as refused:
        platform_verifier().verify(raw)

    assert raw not in str(refused.value)


def test_a_token_is_refused_until_the_key_is_there() -> None:
    verifier = TokenVerifier(StaticKeySource(None))

    with pytest.raises(InvalidTokenError, match="verification key is not available"):
        verifier.verify(platform_token())


async def test_the_verifier_loads_its_key_source() -> None:
    keys = StaticKeySource(None)

    await TokenVerifier(keys).load()

    assert keys.loads == 1


async def test_the_platform_key_is_asked_for_with_the_deployment_token(monkeypatch) -> None:
    """The deployment's own token first, then the key; the key request is a stub for now."""
    token = object()
    jwt_manager = AsyncMock()
    jwt_manager.get_token.return_value = token
    source = PlatformKeySource(jwt_manager=jwt_manager)
    fetch = AsyncMock(return_value=PLATFORM_KEY.public_key())
    monkeypatch.setattr(source, "_fetch_key", fetch)

    await source.load()

    fetch.assert_awaited_once_with(token)
    assert source.current_key() is not None


async def test_without_a_deployment_token_there_is_no_platform_key(caplog) -> None:
    jwt_manager = AsyncMock()
    jwt_manager.get_token.return_value = None
    source = PlatformKeySource(jwt_manager=jwt_manager)

    with caplog.at_level(logging.WARNING):
        await source.load()

    assert source.current_key() is None
    assert "requests the platform signs will be refused" in caplog.text
