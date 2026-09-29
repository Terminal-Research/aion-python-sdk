"""Request tokens as the platform signs them, and a key source that holds a fixed key.

The claims are the ones the platform's example token carries. ``None`` for a
claim in ``platform_token`` leaves it out.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from aion.server.auth import TokenVerifier

PLATFORM_KEY = ec.generate_private_key(ec.SECP256R1())
"""The key the stand-in platform signs with."""

PLATFORM_SUBJECT = "aion:user:user-456"


class StaticKeySource:
    """A key source that always has this key, or none."""

    def __init__(self, key: Optional[ec.EllipticCurvePublicKey]) -> None:
        self.key = key
        self.loads = 0

    async def load(self) -> None:
        self.loads += 1

    def current_key(self) -> Optional[ec.EllipticCurvePublicKey]:
        return self.key


def platform_token(
        subject: Optional[str] = PLATFORM_SUBJECT,
        *,
        key: Any = PLATFORM_KEY,
        algorithm: str = "ES256",
        lifetime: int = 300,
        **claims: Any,
) -> str:
    """A token as the platform signs it, valid for ``lifetime`` seconds from now."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": "https://aion.example",
        "aud": "aion:runtime:version-123:agent:gemma",
        "sub": subject,
        "caller_kind": "authenticated",
        "agent_environment_id": "environment-789",
        "distribution_id": "distribution-321",
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
        "version": 1,
    }
    payload.update(claims)
    return jwt.encode({name: value for name, value in payload.items() if value is not None}, key, algorithm=algorithm)


def platform_verifier() -> TokenVerifier:
    """A verifier that trusts the stand-in platform's key and nothing else."""
    return TokenVerifier(StaticKeySource(PLATFORM_KEY.public_key()))
