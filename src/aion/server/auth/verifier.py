"""Verification of the bearer tokens requests to an agent carry."""

from __future__ import annotations

import base64
import binascii
import json
from enum import Enum
from typing import Any, Optional

import jwt

from aion.core.exceptions import AionError

from .caller import Assurance, AuthenticatedCaller, CredentialKind, GatewayCoordinates
from .jwks import JwksKeySource
from aion.core.principal import ANONYMOUS_SESSION, InvalidPrincipalError, Principal, canonical_uuid

__all__ = [
    "ANONYMOUS_SESSION_AUDIENCE",
    "CLOCK_SKEW_LEEWAY_SECONDS",
    "CONTRACT_VERSION",
    "INVOCATION_TYPE",
    "InvalidTokenError",
    "KeysUnavailableError",
    "MAX_TOKEN_BYTES",
    "SESSION_TYPE",
    "TokenClaim",
    "TokenVerifier",
    "claimed_kind",
]

ALGORITHM = "ES256"

INVOCATION_TYPE = "aion-invocation+jwt"
SESSION_TYPE = "aion-session+jwt"

ANONYMOUS_SESSION_AUDIENCE = "urn:aion:a2a:anonymous-session"

CONTRACT_VERSION = 1

INVOCATION_LIFETIME_SECONDS = 3600
SESSION_LIFETIME_SECONDS = 30 * 24 * 3600

MAX_TOKEN_BYTES = 8 * 1024
"""The longest compact token accepted."""

CLOCK_SKEW_LEEWAY_SECONDS = 30
"""How far the signer's clock and this server's may disagree; applies to ``nbf`` and ``exp`` only."""

_HEADER_PARAMETERS = frozenset({"alg", "typ", "kid"})
"""The only JOSE header parameters a token may carry; ``jku``, ``x5u``, ``jwk``, ``crit`` and ``zip`` among the refused."""

_GATEWAY_CLAIMS = ("owner_agent_identity_id", "edge_agent_environment_id", "terminal_agent_environment_id")

_COMMON_CLAIMS = ["iss", "aud", "sub", "iat", "nbf", "exp", "token_use", "contract_version", "assurance"]


class InvalidTokenError(AionError):
    """A request token that does not verify. Its message says why and never quotes the token.

    The message is the reason the caller receives. ``detail``, when set, is for
    the server log only: it names the values behind a refusal that usually
    points at the server's own configuration rather than at the caller.
    """

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


class KeysUnavailableError(AionError):
    """No verification key is loaded yet, so no token can be verified; the request may be retried."""


class TokenClaim(Enum):
    """What a bearer token claims to be, read without verifying anything; it only chooses the check."""

    AION = "aion"
    """An Aion ``typ`` header: verified in full, and refused if it does not verify."""

    DAMAGED_AION = "damaged_aion"
    """No Aion ``typ``, but an Aion ``token_use`` or a repeated member: refused, never handed elsewhere."""

    FOREIGN = "foreign"
    """Anything else - the application's own credential, say - which this verifier does not judge."""


def claimed_kind(token: str) -> TokenClaim:
    """What ``token`` claims to be.

    Grants nothing: it decides whether the token is Aion's to verify, or
    refuse, or the application's. A token stripped of both its ``typ`` and its
    ``token_use`` cannot be told from an application's credential; whatever
    the application's authentication makes of it, it carries no Aion rights.
    """
    segments = token.split(".")
    if len(segments) != 3:
        return TokenClaim.FOREIGN
    # Nothing longer than an Aion token can be is parsed whole; a header that
    # short still says whether the token claims to be Aion's, and verifying
    # it then refuses it for its length.
    oversized = len(token.encode("utf-8")) > MAX_TOKEN_BYTES
    try:
        header = _parse_segment(segments[0]) if len(segments[0]) <= MAX_TOKEN_BYTES else None
        claims = None if oversized else _parse_segment(segments[1])
    except _DuplicateMemberError:
        return TokenClaim.DAMAGED_AION
    if header is not None and header.get("typ") in (INVOCATION_TYPE, SESSION_TYPE):
        return TokenClaim.AION
    token_use = claims.get("token_use") if claims is not None else None
    if isinstance(token_use, str) and token_use in {kind.value for kind in CredentialKind}:
        return TokenClaim.DAMAGED_AION
    return TokenClaim.FOREIGN


class TokenVerifier:
    """Verifies a request token and names the caller it was issued to.

    Two kinds of token are ES256-signed JWTs with the same key - Aion's,
    published at ``keys`` - and the protected ``typ`` header tells them apart:

    **Invocation tokens** (``typ`` = ``aion-invocation+jwt``,
    ``token_use`` = ``a2a_invocation``): Aion dispatched the request to this
    agent. ``aud`` is exactly ``invocation_audience``, this server's
    ``AION_CLIENT_ID``, and the token carries the receiving agent identity and
    the edge and terminal environments as UUIDs. They live one hour. A
    verifier without an audience refuses them.

    **Anonymous session tokens** (``typ`` = ``aion-session+jwt``,
    ``token_use`` = ``anonymous_session``): a direct client holds a session
    Aion issued. ``aud`` is ``urn:aion:a2a:anonymous-session``, ``sub`` an
    ``AnonymousSession``, ``assurance`` ``session``, and none of the gateway
    coordinates appear. They live 30 days. A verifier built with
    ``accept_sessions=False`` refuses them.

    Both need ``iss`` = ``issuer``, ``contract_version`` = 1, a canonical
    ``sub``, a known ``assurance``, and integer ``iat`` = ``nbf`` with ``exp``
    exactly one lifetime later. Only ``alg``, ``typ`` and ``kid`` may appear in
    the header, and no claim twice. The ``kid`` has to name a loaded key: an
    unknown one is refused without fetching. Times are checked with
    ``CLOCK_SKEW_LEEWAY_SECONDS`` of tolerance and nothing else is.

    ``trust_application_users`` says whether a request without an Aion token
    may still be served as the user the application's own authentication
    installed; the middleware asks. Only a server outside the platform that
    does not require invocation tokens trusts them.
    """

    def __init__(
            self,
            keys: JwksKeySource,
            *,
            issuer: str,
            invocation_audience: Optional[str] = None,
            accept_sessions: bool = True,
            trust_application_users: bool = False,
    ) -> None:
        self.keys = keys
        self.issuer = issuer
        self.invocation_audience = invocation_audience
        self.accept_sessions = accept_sessions
        self.trust_application_users = trust_application_users

    @property
    def accepts_invocations(self) -> bool:
        return self.invocation_audience is not None

    async def load(self) -> None:
        """Get the keys this server verifies with ready."""
        await self.keys.load()

    async def aclose(self) -> None:
        """Release what the key source holds open."""
        await self.keys.aclose()

    async def verify(self, token: str) -> AuthenticatedCaller:
        """The caller ``token`` names.

        Raises:
            InvalidTokenError: The token does not verify.
            KeysUnavailableError: No key is loaded to verify it with.
        """
        if len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
            raise InvalidTokenError(f"the bearer token is longer than {MAX_TOKEN_BYTES} bytes")
        header, unverified_claims = _unverified_parts(token)

        if header.get("alg") != ALGORITHM:
            raise InvalidTokenError("the bearer token is not signed with ES256")
        unsupported = set(header) - _HEADER_PARAMETERS
        if unsupported:
            raise InvalidTokenError(f"the bearer token's header carries unsupported parameters: {sorted(unsupported)}")
        token_type = header.get("typ")
        if token_type == INVOCATION_TYPE:
            if not self.accepts_invocations:
                raise InvalidTokenError("this server does not accept invocation tokens")
            credential, audience = CredentialKind.INVOCATION, self.invocation_audience
        elif token_type == SESSION_TYPE:
            if not self.accept_sessions:
                raise InvalidTokenError("this server does not accept anonymous session tokens")
            credential, audience = CredentialKind.SESSION, ANONYMOUS_SESSION_AUDIENCE
        else:
            raise InvalidTokenError("the bearer token is not an Aion invocation or session token")

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidTokenError("the bearer token has no 'kid' header")
        key = await self.keys.key_for(kid)
        if key is None:
            if not self.keys.available:
                raise KeysUnavailableError("the verification keys are not available yet")
            raise InvalidTokenError("the bearer token is signed with an unknown key")

        _check_claim_types(unverified_claims)
        claims = _decode(token, unverified_claims, key=key.key, audience=audience, issuer=self.issuer)
        return _caller(claims, credential=credential)


def _unverified_parts(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The header and claims of a compact JWS, read without its signature to choose the key.

    Nothing read here is trusted. A member repeated in either is refused: a
    parser that keeps the first and one that keeps the last would disagree
    about what was signed.
    """
    segments = token.split(".")
    if len(segments) != 3 or not all(segments):
        raise InvalidTokenError("the bearer token is not a JWT")
    return _json_segment(segments[0]), _json_segment(segments[1])


def _json_segment(segment: str) -> dict[str, Any]:
    try:
        value = _parse_segment(segment)
    except _DuplicateMemberError as error:
        raise InvalidTokenError(f"the bearer token repeats '{error.name}'") from None
    if value is None:
        raise InvalidTokenError("the bearer token is not a JWT")
    return value


def _parse_segment(segment: str) -> Optional[dict[str, Any]]:
    """A base64url JSON object; ``None`` for anything else.

    Raises:
        _DuplicateMemberError: The object repeats a member.
    """
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        value = json.loads(raw, object_pairs_hook=_no_duplicates)
    except (binascii.Error, ValueError):
        return None
    return value if isinstance(value, dict) else None


class _DuplicateMemberError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    members: dict[str, Any] = {}
    for name, value in pairs:
        if name in members:
            raise _DuplicateMemberError(name)
        members[name] = value
    return members


_STRING_CLAIMS = ("iss", "aud", "sub", "token_use", "assurance")
_INTEGER_CLAIMS = ("iat", "nbf", "exp", "contract_version")


def _check_claim_types(claims: dict[str, Any]) -> None:
    """Refuse claims of the wrong JSON type before PyJWT reads them.

    PyJWT compares and converts these claims itself and expects the types
    the contract fixes; any other type is a malformed token, not a fault of
    the server. A claim that is missing is left to the required-claim check.
    """
    for name in _STRING_CLAIMS:
        if name in claims and not isinstance(claims[name], str):
            raise InvalidTokenError(f"the bearer token's '{name}' is not a string")
    for name in _INTEGER_CLAIMS:
        if name in claims and not _is_int(claims[name]):
            raise InvalidTokenError(f"the bearer token's '{name}' is not a whole number")


def _decode(token: str, unverified_claims: dict[str, Any], *, key: Any, audience: str, issuer: str) -> dict[str, Any]:
    """The verified claims of ``token``.

    A signed Aion token for another audience or issuer usually means the
    deployment is configured for another client or environment, so that
    refusal carries both values as its ``detail`` for the server log.
    """
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={"require": _COMMON_CLAIMS, "strict_aud": True},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("the bearer token has expired") from None
    except jwt.ImmatureSignatureError:
        raise InvalidTokenError("the bearer token is not valid yet") from None
    except jwt.MissingRequiredClaimError as error:
        raise InvalidTokenError(f"the bearer token has no '{error.claim}' claim") from None
    except jwt.InvalidAudienceError:
        raise InvalidTokenError(
            "the bearer token is not addressed to this server",
            detail=f"token aud {unverified_claims.get('aud')!r}, this server expects {audience!r}",
        ) from None
    except jwt.InvalidIssuerError:
        raise InvalidTokenError(
            "the bearer token has an unexpected issuer",
            detail=(
                f"token iss {unverified_claims.get('iss')!r}, "
                f"this server expects {issuer!r} (AION_API_CLIENT_AUTH_ISSUER)"
            ),
        ) from None
    except jwt.InvalidSignatureError:
        raise InvalidTokenError("the bearer token does not verify") from None
    except jwt.DecodeError:
        raise InvalidTokenError("the bearer token is not a JWT") from None
    except jwt.PyJWTError:
        raise InvalidTokenError("the bearer token does not verify") from None
    return claims


def _caller(claims: dict[str, Any], *, credential: CredentialKind) -> AuthenticatedCaller:
    """The caller verified ``claims`` name, once the contract's own rules hold."""
    if claims["token_use"] != credential.value:
        raise InvalidTokenError("the bearer token's 'token_use' does not match its type")
    if claims["contract_version"] != CONTRACT_VERSION:
        raise InvalidTokenError("the bearer token's contract version is not supported")

    issued, not_before, expires = claims["iat"], claims["nbf"], claims["exp"]
    lifetime = INVOCATION_LIFETIME_SECONDS if credential is CredentialKind.INVOCATION else SESSION_LIFETIME_SECONDS
    if not_before != issued or expires - issued != lifetime:
        raise InvalidTokenError("the bearer token's validity window is not the contract's")

    try:
        principal = Principal.from_subject(claims["sub"])
    except InvalidPrincipalError as error:
        raise InvalidTokenError(f"the bearer token's 'sub' is not a canonical principal: {error}") from None

    try:
        assurance = Assurance(claims["assurance"])
    except ValueError:
        raise InvalidTokenError("the bearer token's 'assurance' is not known") from None
    # Assurance is what Aion established, not the principal type; the one
    # rule tying them is that a session, and only a session, holds `session`.
    if (assurance is Assurance.SESSION) != (principal.type == ANONYMOUS_SESSION):
        raise InvalidTokenError("the bearer token's assurance does not fit its principal")

    if credential is CredentialKind.SESSION:
        if principal.type != ANONYMOUS_SESSION:
            raise InvalidTokenError("an anonymous session token has to name an anonymous session")
        present = [name for name in _GATEWAY_CLAIMS if name in claims]
        if present:
            raise InvalidTokenError(f"an anonymous session token carries invocation claims: {present}")
        gateway = None
    else:
        missing = [name for name in _GATEWAY_CLAIMS if name not in claims]
        if missing:
            raise InvalidTokenError(f"the bearer token has no '{missing[0]}' claim")
        malformed = [name for name in _GATEWAY_CLAIMS if canonical_uuid(claims[name]) is None]
        if malformed:
            raise InvalidTokenError(f"the bearer token's '{malformed[0]}' is not a lowercase UUID")
        gateway = GatewayCoordinates(**{name: claims[name] for name in _GATEWAY_CLAIMS})

    return AuthenticatedCaller(
        principal,
        credential=credential,
        assurance=assurance,
        issuer=claims["iss"],
        gateway=gateway,
        claims=claims,
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
