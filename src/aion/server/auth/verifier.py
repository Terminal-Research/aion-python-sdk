"""Verification of the bearer tokens requests to an agent carry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Protocol

import jwt

from aion.core.exceptions import AionError

from .caller import AuthenticatedCaller
from .jwks import JwksKeySource

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric import ec

__all__ = [
    "ANONYMOUS_SESSION_AUDIENCE",
    "ANONYMOUS_SESSION_ISSUER",
    "ANONYMOUS_SESSION_SUBJECT_TYPE",
    "InvalidTokenError",
    "KeySource",
    "TokenVerifier",
]

CALL_ALGORITHM = "ES256"

SESSION_ALGORITHMS = frozenset({"ES256", "ES384", "ES512", "RS256", "PS256", "EdDSA"})
"""Algorithms a session token's key may name. Asymmetric only: ``none`` and ``HS*`` would let a token vouch for itself."""

ANONYMOUS_SESSION_AUDIENCE = "aion-anonymous-session"
ANONYMOUS_SESSION_ISSUER = "aion"
ANONYMOUS_SESSION_SUBJECT_TYPE = "AnonymousSession"

CLOCK_SKEW_LEEWAY_SECONDS = 30
"""How far the signer's clock and this server's may disagree about ``exp`` and ``nbf``."""


class InvalidTokenError(AionError):
    """A request token that does not verify. Its message says why and never quotes the token."""


class KeySource(Protocol):
    """Where the public key call tokens are verified with comes from."""

    async def load(self) -> None:
        """Get the key ready, once, before the server takes requests."""

    def current_key(self) -> Optional[ec.EllipticCurvePublicKey]:
        """The key to verify with now; ``None`` when there is none."""


class TokenVerifier:
    """Verifies a request token and names the caller it was issued to.

    Two kinds of token are signed JWTs (JWS), and ``aud`` tells them apart.
    The audience is read before the signature is checked, only to choose the
    key; the chosen key then verifies the whole token, ``aud`` included.

    **Call tokens** carry this server's ``AION_CLIENT_ID`` as ``aud`` and are
    ES256, signed with the key ``call_keys`` holds. They need ``aud``, ``exp``
    and ``sub``. A verifier built without ``call_keys`` refuses them.

    **Anonymous session tokens** carry ``aud`` = ``aion-anonymous-session`` and
    are signed with a key the control plane publishes, found by the token's
    ``kid`` in ``session_keys``; the algorithm is the key's own, limited to
    ``SESSION_ALGORITHMS``. They need ``iss`` = ``aion``, ``aud``,
    ``subject_type`` = ``AnonymousSession``, ``exp`` and ``sub``. A verifier
    built without ``session_keys`` refuses them.

    Every token also has to be current (``exp``, and ``nbf`` when it has one).
    The caller is named by ``sub``, and all claims stay on it.
    """

    def __init__(
            self,
            *,
            call_keys: Optional[KeySource] = None,
            call_audience: Optional[str] = None,
            session_keys: Optional[JwksKeySource] = None,
    ) -> None:
        self.call_keys = call_keys
        self.call_audience = call_audience
        self.session_keys = session_keys

    async def load(self) -> None:
        """Get the keys this server verifies with ready."""
        if self.call_keys is not None:
            await self.call_keys.load()
        if self.session_keys is not None:
            await self.session_keys.load()

    async def aclose(self) -> None:
        """Release what the key sources hold open."""
        if self.session_keys is not None:
            await self.session_keys.aclose()

    async def verify(self, token: str) -> AuthenticatedCaller:
        """The caller ``token`` names.

        Raises:
            InvalidTokenError: The token does not verify.
        """
        audiences = _unverified_audiences(token)
        if ANONYMOUS_SESSION_AUDIENCE in audiences:
            if self.session_keys is None:
                raise InvalidTokenError("this server does not accept anonymous session tokens")
            claims = await self._verify_session(token)
        elif self.call_audience is not None and self.call_audience in audiences:
            if self.call_keys is None:
                raise InvalidTokenError("this server does not accept the bearer token")
            claims = self._verify_call(token)
        else:
            raise InvalidTokenError("the bearer token is not addressed to this server")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidTokenError("the bearer token names no caller in 'sub'")
        return AuthenticatedCaller(subject=subject, issuer=claims.get("iss"), claims=claims)

    def _verify_call(self, token: str) -> dict[str, Any]:
        key = self.call_keys.current_key()
        if key is None:
            raise InvalidTokenError("the verification key is not available")
        return _decode(
            token,
            key=key,
            algorithm=CALL_ALGORITHM,
            audience=self.call_audience,
            required=["aud", "exp", "sub"],
        )

    async def _verify_session(self, token: str) -> dict[str, Any]:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError:
            raise InvalidTokenError("the bearer token is not a JWT") from None
        if not isinstance(kid, str) or not kid:
            raise InvalidTokenError("the bearer token has no 'kid' header")
        key = await self.session_keys.key_for(kid)
        if key is None:
            raise InvalidTokenError("the verification key for this token is not available")
        algorithm = key.algorithm_name
        if algorithm not in SESSION_ALGORITHMS:
            raise InvalidTokenError("the bearer token's key does not allow an accepted algorithm")
        claims = _decode(
            token,
            key=key.key,
            algorithm=algorithm,
            audience=ANONYMOUS_SESSION_AUDIENCE,
            issuer=ANONYMOUS_SESSION_ISSUER,
            required=["iss", "aud", "subject_type", "exp", "sub"],
        )
        if claims["subject_type"] != ANONYMOUS_SESSION_SUBJECT_TYPE:
            raise InvalidTokenError("the bearer token does not name an anonymous session")
        return claims


def _unverified_audiences(token: str) -> list[str]:
    """The ``aud`` a token claims, read without checking its signature.

    Only for choosing which key verifies the token: nothing read here is
    trusted.
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        raise InvalidTokenError("the bearer token is not a JWT") from None
    audience = payload.get("aud")
    if isinstance(audience, str):
        return [audience]
    if isinstance(audience, list):
        return [item for item in audience if isinstance(item, str)]
    return []


def _decode(
        token: str,
        *,
        key: Any,
        algorithm: str,
        audience: str,
        required: list[str],
        issuer: Optional[str] = None,
) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            key=key,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={"require": required},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("the bearer token has expired") from None
    except jwt.ImmatureSignatureError:
        raise InvalidTokenError("the bearer token is not valid yet") from None
    except jwt.MissingRequiredClaimError as error:
        raise InvalidTokenError(f"the bearer token has no '{error.claim}' claim") from None
    except jwt.InvalidAudienceError:
        raise InvalidTokenError("the bearer token is not addressed to this server") from None
    except jwt.InvalidIssuerError:
        raise InvalidTokenError("the bearer token has an unexpected issuer") from None
    except jwt.InvalidSignatureError:
        raise InvalidTokenError("the bearer token does not verify") from None
    except jwt.DecodeError:
        raise InvalidTokenError("the bearer token is not a JWT") from None
    except jwt.PyJWTError:
        raise InvalidTokenError("the bearer token does not verify") from None
