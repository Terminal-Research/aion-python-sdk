"""Which bearer tokens ``TokenVerifier`` accepts under contract A, and the caller it names from one."""

import base64
import json
import time
import uuid

import jwt
import pytest

from aion.server.auth import (
    Assurance,
    CredentialKind,
    GatewayCoordinates,
    InvalidTokenError,
    KeysUnavailableError,
    Principal,
    TokenClaim,
    claimed_kind,
)
from tests.support.aion_tokens import (
    EDGE_ENVIRONMENT_ID,
    OWNER_AGENT_IDENTITY_ID,
    TERMINAL_ENVIRONMENT_ID,
    SigningKey,
    subject,
)
from tests.unit.support.tokens import AION_KEY, ControlPlane, hosted_verifier, session_verifier, verifier

USER_ID = "7a9e2b1c-1111-4d2e-8f3a-6b5c4d3e2f10"
SESSION_ID = "f9d7daea-df95-4111-8533-5d6f043edaf9"


def _raw_subject(principal_type: str, principal_id: str) -> str:
    """A subject encoded as the contract does, without checking the ID: for IDs the verifier must refuse."""
    return f"aion:v1:{principal_type}:" + base64.urlsafe_b64encode(principal_id.encode()).rstrip(b"=").decode()


async def _verify(token: str, make=verifier):
    built = make()
    await built.load()
    return await built.verify(token)


async def _refused(token: str, make=verifier, match: str | None = None) -> InvalidTokenError:
    built = make()
    await built.load()
    with pytest.raises(InvalidTokenError, match=match) as refused:
        await built.verify(token)
    assert token not in str(refused.value)
    return refused.value


async def test_an_invocation_token_names_a_typed_caller() -> None:
    caller = await _verify(AION_KEY.invocation_token(USER_ID))

    assert caller.principal == Principal("AionUser", USER_ID)
    assert (caller.subject, caller.display_name, caller.is_authenticated) == (
        subject("AionUser", USER_ID),
        subject("AionUser", USER_ID),
        True,
    )
    assert (caller.credential, caller.assurance, caller.issuer) == (
        CredentialKind.INVOCATION,
        Assurance.ACCOUNT,
        "aion.io",
    )
    assert caller.gateway == GatewayCoordinates(OWNER_AGENT_IDENTITY_ID, EDGE_ENVIRONMENT_ID, TERMINAL_ENVIRONMENT_ID)
    assert caller.session_id is None


async def test_a_session_token_names_the_session() -> None:
    caller = await _verify(AION_KEY.session_token(SESSION_ID))

    assert caller.principal == Principal("AnonymousSession", SESSION_ID)
    assert (caller.credential, caller.assurance, caller.gateway, caller.session_id) == (
        CredentialKind.SESSION,
        Assurance.SESSION,
        None,
        SESSION_ID,
    )


@pytest.mark.parametrize("assurance", ["account", "runtime", "provider", "internal", "unattributed"])
async def test_an_invocation_carries_the_assurance_aion_established(assurance) -> None:
    caller = await _verify(AION_KEY.invocation_token(
        "v1:slack:VDE:VTI", assurance=assurance, principal_type="ExternalSender"
    ))

    assert caller.assurance == Assurance(assurance)


async def test_an_invocation_may_come_from_an_anonymous_session() -> None:
    """A public distribution's guest reaches the agent through Aion with session assurance."""
    caller = await _verify(
        AION_KEY.invocation_token(SESSION_ID, principal_type="AnonymousSession", assurance="session")
    )

    assert (caller.credential, caller.assurance, caller.principal.type) == (
        CredentialKind.INVOCATION,
        Assurance.SESSION,
        "AnonymousSession",
    )


@pytest.mark.parametrize(
    ("make", "token", "accepted"),
    [
        (hosted_verifier, "invocation", True),
        (hosted_verifier, "session", False),
        (verifier, "invocation", True),
        (verifier, "session", True),
        (session_verifier, "invocation", False),
        (session_verifier, "session", True),
    ],
    ids=["hosted-invocation", "hosted-session", "client-id-invocation", "client-id-session",
         "no-client-id-invocation", "no-client-id-session"],
)
async def test_the_mode_decides_which_kind_is_accepted(make, token, accepted) -> None:
    raw = AION_KEY.invocation_token() if token == "invocation" else AION_KEY.session_token()
    if accepted:
        await _verify(raw, make)
    else:
        await _refused(raw, make, match="does not accept")


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (lambda: AION_KEY.invocation_token(audience="another-client"), "not addressed"),
        (lambda: AION_KEY.invocation_token(audience=["client-123"]), "'aud' is not a string"),
        (lambda: AION_KEY.invocation_token(audience=None), "'aud'"),
        (lambda: AION_KEY.invocation_token(iss="someone-else"), "issuer"),
        (lambda: AION_KEY.invocation_token(iss=None), "'iss'"),
        (lambda: AION_KEY.invocation_token(sub=None), "'sub'"),
        (lambda: AION_KEY.invocation_token(sub="aion:user:alice"), "canonical principal"),
        (lambda: AION_KEY.invocation_token(sub=_raw_subject("ExternalSender", "AbC")), "external sender"),
        (lambda: AION_KEY.invocation_token(sub=_raw_subject("ExternalIdentity", "x")), "external identity"),
        (lambda: AION_KEY.invocation_token(sub=42), "'sub' is not a string"),
        (lambda: AION_KEY.invocation_token(token_use="anonymous_session"), "token_use"),
        (lambda: AION_KEY.invocation_token(token_use=None), "'token_use'"),
        (lambda: AION_KEY.invocation_token(contract_version=2), "contract version"),
        (lambda: AION_KEY.invocation_token(contract_version="1"), "'contract_version' is not a whole number"),
        (lambda: AION_KEY.invocation_token(contract_version=True), "'contract_version' is not a whole number"),
        (lambda: AION_KEY.invocation_token(contract_version=None), "'contract_version'"),
        (lambda: AION_KEY.invocation_token(assurance="admin"), "'assurance'"),
        (lambda: AION_KEY.invocation_token(assurance=None), "'assurance'"),
        (lambda: AION_KEY.invocation_token(assurance="session"), "does not fit"),
        (lambda: AION_KEY.invocation_token(SESSION_ID, principal_type="AnonymousSession"), "does not fit"),
        (lambda: AION_KEY.invocation_token(owner_agent_identity_id=None), "'owner_agent_identity_id'"),
        (lambda: AION_KEY.invocation_token(edge_agent_environment_id=None), "'edge_agent_environment_id'"),
        (lambda: AION_KEY.invocation_token(terminal_agent_environment_id=None), "'terminal_agent_environment_id'"),
        (lambda: AION_KEY.invocation_token(edge_agent_environment_id="env-1"), "lowercase UUID"),
        (lambda: AION_KEY.invocation_token(owner_agent_identity_id=OWNER_AGENT_IDENTITY_ID.upper()), "lowercase UUID"),
        (lambda: AION_KEY.invocation_token(owner_agent_identity_id=7), "lowercase UUID"),
    ],
    ids=["other-aud", "aud-list", "no-aud", "other-iss", "no-iss", "no-sub", "legacy-sub", "malformed-sender",
         "malformed-identity", "sub-not-text",
         "wrong-token-use", "no-token-use", "version-2", "version-text", "version-bool", "no-version",
         "unknown-assurance", "no-assurance", "session-assurance-for-a-user", "session-without-session-assurance",
         "no-owner", "no-edge", "no-terminal", "edge-not-uuid", "owner-uppercase", "owner-not-text"],
)
async def test_an_invocation_token_off_the_contract_is_refused(token, reason) -> None:
    await _refused(token(), match=reason)


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (lambda: AION_KEY.session_token(aud="client-123"), "not addressed"),
        (lambda: AION_KEY.session_token(sub=subject("AionUser", USER_ID)), "does not fit|anonymous session"),
        (lambda: AION_KEY.session_token(sub=subject("AionUser", USER_ID), assurance="account"), "anonymous session"),
        (lambda: AION_KEY.session_token(assurance="account"), "does not fit"),
        (lambda: AION_KEY.session_token(token_use="a2a_invocation"), "token_use"),
        (lambda: AION_KEY.session_token(owner_agent_identity_id=OWNER_AGENT_IDENTITY_ID), "invocation claims"),
        (lambda: AION_KEY.session_token(sub="aion:v1:AnonymousSession:c2Vzc2lvbg"), "lowercase UUID"),
    ],
    ids=["invocation-aud", "user-subject", "user-subject-account", "account-assurance", "wrong-token-use",
         "gateway-claim", "session-id-not-uuid"],
)
async def test_a_session_token_off_the_contract_is_refused(token, reason) -> None:
    await _refused(token(), match=reason)


@pytest.mark.parametrize(
    ("issued_at", "claims", "reason"),
    [
        (lambda now: now - 3600 - 31, {}, "expired"),
        (lambda now: now + 31, {}, "not valid yet"),
        (lambda now: now, {"nbf": "later"}, "'nbf' is not a whole number"),
        (lambda now: now, {"exp": "later"}, "'exp' is not a whole number"),
        (lambda now: now, {"iat": 1.5}, "'iat' is not a whole number"),
        (lambda now: now, {"nbf": None}, "'nbf'"),
        (lambda now: now, {"iat": None}, "'iat'"),
        (lambda now: now, {"exp": None}, "'exp'"),
    ],
    ids=["expired", "not-yet-valid", "nbf-text", "exp-text", "iat-float", "no-nbf", "no-iat", "no-exp"],
)
async def test_an_invocation_token_has_to_be_current(issued_at, claims, reason) -> None:
    await _refused(AION_KEY.invocation_token(issued_at=issued_at(int(time.time())), **claims), match=reason)


async def test_the_clock_tolerance_is_thirty_seconds() -> None:
    now = int(time.time())

    await _verify(AION_KEY.invocation_token(issued_at=now - 3600 - 25))
    await _verify(AION_KEY.invocation_token(issued_at=now + 25))


@pytest.mark.parametrize(
    ("claims", "kind"),
    [
        (lambda now: {"nbf": now + 1}, "invocation"),
        (lambda now: {"exp": now + 3599}, "invocation"),
        (lambda now: {"exp": now + 7200}, "invocation"),
        (lambda now: {"exp": now + 3600}, "session"),
    ],
    ids=["nbf-after-iat", "short-invocation", "long-invocation", "session-one-hour"],
)
async def test_the_validity_window_is_exactly_the_contracts(claims, kind) -> None:
    now = int(time.time())
    make = AION_KEY.invocation_token if kind == "invocation" else AION_KEY.session_token

    await _refused(make(issued_at=now, **claims(now)), match="validity window")


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"typ": None}, "not an Aion"),
        ({"typ": "JWT"}, "not an Aion"),
        ({"kid": None}, "'kid'"),
        ({"kid": "unknown"}, "unknown key"),
        ({"jku": "https://evil.example/keys"}, "unsupported parameters"),
        ({"x5u": "https://evil.example/cert"}, "unsupported parameters"),
        ({"jwk": AION_KEY.public_jwk()}, "unsupported parameters"),
        ({"crit": ["exp"]}, "unsupported parameters"),
        ({"zip": "DEF"}, "unsupported parameters"),
        ({"cty": "JWT"}, "unsupported parameters"),
    ],
    ids=["no-typ", "plain-jwt-typ", "no-kid", "unknown-kid", "jku", "x5u", "embedded-jwk", "crit", "zip", "cty"],
)
async def test_a_header_off_the_contract_is_refused(headers, reason) -> None:
    await _refused(AION_KEY.invocation_token(headers=headers), match=reason)


async def test_a_token_signed_with_another_key_under_the_kid_is_refused() -> None:
    forger = SigningKey()
    forger.kid = AION_KEY.kid

    await _refused(forger.invocation_token(), match="does not verify")


@pytest.mark.parametrize(
    "token",
    [
        lambda: jwt.encode({"sub": "s"}, "a-shared-secret-long-enough-for-hs256-0123456789",
                           algorithm="HS256", headers={"typ": "aion-invocation+jwt", "kid": AION_KEY.kid}),
        lambda: jwt.encode({"sub": "s"}, None, algorithm="none",
                           headers={"typ": "aion-invocation+jwt", "kid": AION_KEY.kid}),
    ],
    ids=["hs256", "none"],
)
async def test_only_es256_is_accepted(token) -> None:
    await _refused(token(), match="ES256|not a JWT")


@pytest.mark.parametrize(
    "header_payload",
    [
        lambda: ('{"alg":"ES256","typ":"aion-invocation+jwt","kid":"%s","kid":"%s"}' % (AION_KEY.kid, AION_KEY.kid),
                 "{}"),
        lambda: (AION_KEY.header_json(), _payload_json_with_repeated_sub()),
    ],
    ids=["header-member", "claim"],
)
async def test_a_repeated_member_is_refused(header_payload) -> None:
    header, payload = header_payload()

    await _refused(AION_KEY.sign_raw(header, payload), match="repeats")


def _payload_json_with_repeated_sub() -> str:
    token = AION_KEY.invocation_token(USER_ID)
    claims = jwt.decode(token, options={"verify_signature": False})
    text = json.dumps(claims)
    return text[:-1] + ', "sub": "%s"}' % subject("AionUser", "mallory")


@pytest.mark.parametrize("name", ["iss", "aud", "sub", "token_use", "assurance"])
@pytest.mark.parametrize("value", [[], {}, 1, True], ids=["list", "object", "number", "boolean"])
async def test_a_string_claim_of_another_type_is_refused(name, value) -> None:
    await _refused(_signed_claims(**{name: value}), match=f"'{name}' is not a string")


@pytest.mark.parametrize("name", ["iat", "nbf", "exp", "contract_version"])
@pytest.mark.parametrize("value", [[], {}, "1", True, 1.5], ids=["list", "object", "string", "boolean", "fraction"])
async def test_an_integer_claim_of_another_type_is_refused(name, value) -> None:
    await _refused(_signed_claims(**{name: value}), match=f"'{name}' is not a whole number")


def _signed_claims(**replaced) -> str:
    """A valid invocation token's claims with ``replaced`` in them, signed as they are."""
    claims = jwt.decode(AION_KEY.invocation_token(USER_ID), options={"verify_signature": False})
    return AION_KEY.sign_raw(AION_KEY.header_json(), json.dumps({**claims, **replaced}))


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b", "a..c", "e30.e30.", "W10.e30.sig"])
async def test_something_that_is_not_a_jwt_is_refused(token) -> None:
    await _refused(token, match="not a JWT")


async def test_a_token_over_8_kib_is_refused_unread() -> None:
    await _refused(AION_KEY.invocation_token(padding="x" * 8192), match="8192 bytes")


async def test_a_principal_id_over_1024_bytes_is_refused() -> None:
    encoded = base64.urlsafe_b64encode(b"x" * 1025).rstrip(b"=").decode()

    await _refused(AION_KEY.invocation_token(sub=f"aion:v1:AionUser:{encoded}"), match="1024")


async def test_without_loaded_keys_a_token_cannot_be_verified() -> None:
    """Not a refusal: the token is neither accepted nor declared invalid until the keys are there."""
    control_plane = ControlPlane()
    control_plane.failing = True
    built = verifier(control_plane)
    await built.load()

    with pytest.raises(KeysUnavailableError):
        await built.verify(AION_KEY.invocation_token())


async def test_a_token_the_mode_refuses_costs_no_key_lookup() -> None:
    control_plane = ControlPlane()
    built = hosted_verifier(control_plane)

    with pytest.raises(InvalidTokenError):
        await built.verify(AION_KEY.session_token())

    assert control_plane.requests == 0


async def test_the_issuer_is_the_configured_one() -> None:
    control_plane = ControlPlane()
    built = verifier(control_plane)
    built.issuer = "aion.staging"
    await built.load()

    with pytest.raises(InvalidTokenError, match="issuer"):
        await built.verify(AION_KEY.invocation_token())
    caller = await built.verify(AION_KEY.invocation_token(iss="aion.staging"))
    assert caller.issuer == "aion.staging"


async def test_every_session_is_its_own_caller() -> None:
    first, second = str(uuid.uuid4()), str(uuid.uuid4())

    assert (await _verify(AION_KEY.session_token(first))).subject != (
        await _verify(AION_KEY.session_token(second))
    ).subject


@pytest.mark.parametrize(
    ("token", "claim"),
    [
        (lambda: AION_KEY.invocation_token(), TokenClaim.AION),
        (lambda: AION_KEY.session_token(), TokenClaim.AION),
        (lambda: AION_KEY.invocation_token(audience="elsewhere", iss="x"), TokenClaim.AION),
        (lambda: AION_KEY.invocation_token(headers={"typ": None}), TokenClaim.DAMAGED_AION),
        (lambda: AION_KEY.session_token(headers={"typ": "JWT"}), TokenClaim.DAMAGED_AION),
        (lambda: AION_KEY.sign_raw(
            '{"alg":"ES256","typ":"JWT","typ":"aion-invocation+jwt"}', "{}"), TokenClaim.DAMAGED_AION),
        (lambda: jwt.encode({"sub": "alice"}, "an-application-secret-long-enough-for-hs256", algorithm="HS256"),
         TokenClaim.FOREIGN),
        (lambda: AION_KEY.invocation_token(headers={"typ": None}, token_use=None), TokenClaim.FOREIGN),
        (lambda: "an-opaque-application-key", TokenClaim.FOREIGN),
        (lambda: "a.b.c", TokenClaim.FOREIGN),
        (lambda: AION_KEY.invocation_token(headers={"typ": None}, token_use=[]), TokenClaim.FOREIGN),
        (lambda: AION_KEY.invocation_token(headers={"typ": None}, token_use={}), TokenClaim.FOREIGN),
        (lambda: AION_KEY.invocation_token(headers={"typ": ["aion-invocation+jwt"]}), TokenClaim.DAMAGED_AION),
        (lambda: AION_KEY.invocation_token(padding="x" * 8192), TokenClaim.AION),
        (lambda: AION_KEY.invocation_token(headers={"typ": None}, padding="x" * 8192), TokenClaim.FOREIGN),
    ],
    ids=["invocation", "session", "aion-typ-wrong-claims", "no-typ-aion-use", "jwt-typ-aion-use",
         "repeated-typ", "application-jwt", "stripped-aion", "opaque", "not-json", "token-use-list",
         "token-use-object", "typ-list", "oversized-aion", "oversized-unread-claims"],
)
def test_what_a_token_claims_chooses_the_check_and_nothing_more(token, claim) -> None:
    assert claimed_kind(token()) is claim
