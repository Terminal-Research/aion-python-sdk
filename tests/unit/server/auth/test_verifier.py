"""Which bearer tokens ``TokenVerifier`` accepts, and the caller it names from one."""

import base64
import logging
import time
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from jwt.algorithms import OKPAlgorithm

from aion.server.auth import InvalidTokenError, PlatformKeySource, TokenVerifier
from tests.unit.support.tokens import (
    CLIENT_ID,
    PLATFORM_KEY,
    PLATFORM_SUBJECT,
    SESSION_KID,
    SESSION_SUBJECT,
    ControlPlane,
    StaticKeySource,
    jwks_document,
    open_verifier,
    platform_token,
    platform_verifier,
    session_token,
    session_verifier,
)


async def test_a_call_token_names_its_subject_and_keeps_its_claims() -> None:
    caller = await platform_verifier().verify(platform_token())

    assert (caller.subject, caller.display_name, caller.is_authenticated) == (PLATFORM_SUBJECT, PLATFORM_SUBJECT, True)
    assert caller.issuer == "https://aion.example"
    assert caller.claims["distribution_id"] == "distribution-321"
    assert caller.claims["caller_kind"] == "authenticated"


async def test_a_call_token_may_be_an_anonymous_session_subject() -> None:
    """``subject_type`` is a claim like any other on a call token; nothing limits it."""
    caller = await platform_verifier().verify(platform_token(subject_type="AnonymousSession"))

    assert caller.claims["subject_type"] == "AnonymousSession"


async def test_an_anonymous_session_token_names_its_subject_and_keeps_its_claims() -> None:
    caller = await session_verifier().verify(session_token())

    assert (caller.subject, caller.is_authenticated, caller.issuer) == (SESSION_SUBJECT, True, "aion")
    assert caller.claims["subject_type"] == "AnonymousSession"


@pytest.mark.parametrize(
    ("verifier", "token", "accepted"),
    [
        ("hosted", "call", True),
        ("hosted", "session", False),
        ("credentials", "call", True),
        ("credentials", "session", True),
        ("no-credentials", "call", False),
        ("no-credentials", "session", True),
    ],
)
async def test_the_mode_decides_which_kind_of_token_is_accepted(verifier, token, accepted) -> None:
    """Hosted servers take call tokens only; elsewhere sessions are open and call tokens need credentials."""
    control_plane = ControlPlane()
    verifiers = {
        "hosted": platform_verifier(),
        "credentials": open_verifier(control_plane),
        "no-credentials": session_verifier(control_plane),
    }
    tokens = {"call": platform_token(), "session": session_token()}
    requests_before = control_plane.requests

    if accepted:
        await verifiers[verifier].verify(tokens[token])
    else:
        with pytest.raises(InvalidTokenError):
            await verifiers[verifier].verify(tokens[token])

    if verifier == "hosted":
        assert control_plane.requests == requests_before


@pytest.mark.parametrize(
    "token",
    [
        lambda: platform_token(lifetime=-120),
        lambda: platform_token(nbf=int(time.time()) + 600),
    ],
    ids=["expired", "not-yet-valid"],
)
async def test_a_call_token_has_to_be_current(token) -> None:
    with pytest.raises(InvalidTokenError, match="expired|not valid yet"):
        await platform_verifier().verify(token())


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (lambda: platform_token(None), "'sub'"),
        (lambda: platform_token(""), "'sub'"),
        (lambda: platform_token(exp=None), "'exp'"),
        (lambda: platform_token(aud=None), "not addressed"),
        (lambda: platform_token(aud="another-client"), "not addressed"),
        (lambda: platform_token(key=ec.generate_private_key(ec.SECP256R1())), "does not verify"),
        (lambda: platform_token(key="a-shared-secret-long-enough-for-hs256", algorithm="HS256"), "does not verify"),
        (lambda: jwt.encode({"sub": "s", "aud": CLIENT_ID, "exp": int(time.time()) + 60}, None, algorithm="none"),
         "does not verify"),
        (lambda: "not-a-jwt", "not a JWT"),
    ],
    ids=["no-sub", "empty-sub", "no-exp", "no-aud", "other-aud", "other-key", "hs256", "alg-none", "garbage"],
)
async def test_a_call_token_that_does_not_verify_is_refused(token, reason) -> None:
    raw = token()

    with pytest.raises(InvalidTokenError, match=reason) as refused:
        await platform_verifier().verify(raw)

    assert raw not in str(refused.value)


async def test_the_call_audience_may_be_one_of_several() -> None:
    caller = await platform_verifier().verify(platform_token(aud=["another", CLIENT_ID]))

    assert caller.subject == PLATFORM_SUBJECT


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"sub": None}, "'sub'"),
        ({"exp": None}, "'exp'"),
        ({"iss": None}, "'iss'"),
        ({"subject_type": None}, "'subject_type'"),
        ({"iss": "someone-else"}, "issuer"),
        ({"subject_type": "User"}, "anonymous session"),
        ({"lifetime": -120}, "expired"),
        ({"nbf": int(time.time()) + 600}, "not valid yet"),
    ],
    ids=["no-sub", "no-exp", "no-iss", "no-subject-type", "other-iss", "other-subject-type", "expired", "not-yet-valid"],
)
async def test_an_anonymous_session_token_needs_every_claim(claims, reason) -> None:
    raw = session_token(**claims)

    with pytest.raises(InvalidTokenError, match=reason) as refused:
        await session_verifier().verify(raw)

    assert raw not in str(refused.value)


@pytest.mark.parametrize(
    "token",
    [
        lambda: session_token(aud=None),
        lambda: session_token(aud="another-audience"),
        lambda: platform_token(aud="aion:runtime:another"),
    ],
    ids=["no-aud", "other-aud", "call-token-for-another-server"],
)
async def test_a_token_addressed_to_neither_audience_is_refused(token) -> None:
    with pytest.raises(InvalidTokenError, match="not addressed"):
        await open_verifier().verify(token())


async def test_the_audience_may_be_a_list() -> None:
    caller = await session_verifier().verify(session_token(aud=["aion-anonymous-session", "other"]))

    assert caller.subject == SESSION_SUBJECT


async def test_an_unknown_audience_costs_no_key_lookup() -> None:
    control_plane = ControlPlane()

    with pytest.raises(InvalidTokenError):
        await open_verifier(control_plane).verify(session_token(aud="another-audience"))

    assert control_plane.requests == 0


@pytest.mark.parametrize(
    "token",
    [
        lambda: session_token(key=ec.generate_private_key(ec.SECP256R1())),
        lambda: session_token(kid=None),
        lambda: session_token(kid="unknown"),
        lambda: "not-a-jwt",
    ],
    ids=["other-key", "no-kid", "unknown-kid", "garbage"],
)
async def test_an_anonymous_session_token_the_keys_do_not_verify_is_refused(token) -> None:
    with pytest.raises(InvalidTokenError):
        await session_verifier().verify(token())


async def test_a_session_key_names_its_own_algorithm() -> None:
    key = ec.generate_private_key(ec.SECP384R1())
    control_plane = ControlPlane(jwks_document(**{"p384": key.public_key()}))

    caller = await session_verifier(control_plane).verify(session_token(key=key, kid="p384", algorithm="ES384"))

    assert caller.subject == SESSION_SUBJECT


async def test_a_session_token_may_use_eddsa() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    document = {"keys": [{**OKPAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "ed", "alg": "EdDSA"}]}

    caller = await session_verifier(ControlPlane(document)).verify(
        session_token(key=key, kid="ed", algorithm="EdDSA")
    )

    assert caller.subject == SESSION_SUBJECT


async def test_a_session_token_cannot_choose_another_algorithm_than_its_key_names() -> None:
    """The algorithm is the key's, not the token header's."""
    other = ec.generate_private_key(ec.SECP384R1())

    with pytest.raises(InvalidTokenError, match="does not verify"):
        await session_verifier().verify(session_token(key=other, algorithm="ES384"))


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
async def test_a_symmetric_algorithm_is_never_accepted(algorithm) -> None:
    """Neither a shared-secret key in the set nor a token that signs with the public key vouches for itself."""
    secret = "a-shared-secret-long-enough-for-every-hs-variant-0123456789-0123456789abcdef"
    document = {
        "keys": [
            {
                "kty": "oct",
                "kid": "shared",
                "alg": algorithm,
                "k": base64.urlsafe_b64encode(secret.encode()).rstrip(b"=").decode(),
            }
        ]
    }

    with pytest.raises(InvalidTokenError, match="algorithm"):
        await session_verifier(ControlPlane(document)).verify(
            session_token(key=secret, kid="shared", algorithm=algorithm)
        )


async def test_an_unsigned_token_is_never_accepted() -> None:
    unsigned = jwt.encode(
        {
            "iss": "aion",
            "aud": "aion-anonymous-session",
            "sub": SESSION_SUBJECT,
            "subject_type": "AnonymousSession",
            "exp": int(time.time()) + 60,
        },
        None,
        algorithm="none",
        headers={"kid": SESSION_KID},
    )

    with pytest.raises(InvalidTokenError):
        await session_verifier().verify(unsigned)


async def test_a_token_is_refused_until_the_call_key_is_there() -> None:
    verifier = TokenVerifier(call_keys=StaticKeySource(None), call_audience=CLIENT_ID)

    with pytest.raises(InvalidTokenError, match="verification key is not available"):
        await verifier.verify(platform_token())


async def test_the_verifier_loads_its_key_sources() -> None:
    call_keys = StaticKeySource(None)
    control_plane = ControlPlane()

    await TokenVerifier(call_keys=call_keys, call_audience=CLIENT_ID, session_keys=control_plane.key_source()).load()

    assert (call_keys.loads, control_plane.requests) == (1, 1)


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
