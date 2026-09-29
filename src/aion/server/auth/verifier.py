"""Verification of the bearer tokens requests to an agent carry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol

import jwt

from aion.core.exceptions import AionError

from .caller import AuthenticatedCaller

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric import ec

__all__ = [
    "InvalidTokenError",
    "KeySource",
    "TokenVerifier",
]

SIGNING_ALGORITHM = "ES256"

CLOCK_SKEW_LEEWAY_SECONDS = 30
"""How far the signer's clock and this server's may disagree about ``exp`` and ``nbf``."""


class InvalidTokenError(AionError):
    """A request token that does not verify. Its message says why and never quotes the token."""


class KeySource(Protocol):
    """Where the public key tokens are verified with comes from."""

    async def load(self) -> None:
        """Get the key ready, once, before the server takes requests."""

    def current_key(self) -> Optional[ec.EllipticCurvePublicKey]:
        """The key to verify with now; ``None`` when there is none."""


class TokenVerifier:
    """Verifies a request token and names the caller it was issued to.

    A token is ES256, signed with the key its ``key_source`` holds - the
    platform's key for this deployment, as ``build_token_verifier`` builds it.
    It has to be current (``exp``, and ``nbf`` when it has one) and name its
    caller in ``sub``. ``aud`` is not checked.
    """

    def __init__(self, key_source: KeySource) -> None:
        self.key_source = key_source

    async def load(self) -> None:
        """Get the key this server verifies with ready."""
        await self.key_source.load()

    def verify(self, token: str) -> AuthenticatedCaller:
        """The caller ``token`` names.

        Raises:
            InvalidTokenError: The token does not verify.
        """
        key = self.key_source.current_key()
        if key is None:
            raise InvalidTokenError("the verification key is not available")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[SIGNING_ALGORITHM],
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                options={"require": ["exp", "sub"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise InvalidTokenError("the bearer token has expired") from None
        except jwt.ImmatureSignatureError:
            raise InvalidTokenError("the bearer token is not valid yet") from None
        except jwt.MissingRequiredClaimError as error:
            raise InvalidTokenError(f"the bearer token has no '{error.claim}' claim") from None
        except jwt.InvalidSignatureError:
            raise InvalidTokenError("the bearer token does not verify") from None
        except jwt.DecodeError:
            raise InvalidTokenError("the bearer token is not a JWT") from None
        except jwt.PyJWTError:
            raise InvalidTokenError("the bearer token does not verify") from None

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidTokenError("the bearer token names no caller in 'sub'")
        return AuthenticatedCaller(subject=subject, issuer=claims.get("iss"), claims=claims)
