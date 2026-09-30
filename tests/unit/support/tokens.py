"""Request tokens as Aion signs them, and stand-ins for the keys they are verified with.

Call tokens carry the claims the platform's example token has; anonymous
session tokens the ones the control plane issues. ``None`` for a claim leaves
it out. The control plane's key set is served by an ``httpx.MockTransport``.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from aion.server.auth import JwksKeySource, TokenVerifier

PLATFORM_KEY = ec.generate_private_key(ec.SECP256R1())
"""The key the stand-in platform signs with."""

PLATFORM_SUBJECT = "aion:user:user-456"

CLIENT_ID = "client-123"
"""The ``AION_CLIENT_ID`` of the server under test: the audience of its call tokens."""

SESSION_KEY = ec.generate_private_key(ec.SECP256R1())
"""The key the stand-in control plane signs anonymous session tokens with."""

SESSION_KID = "session-key-1"
SESSION_SUBJECT = "aion:anonymous:session-789"
JWKS_URL = "https://api.aion.example/runtime/a2a/verification-keys"


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
        "aud": CLIENT_ID,
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


def session_token(
        subject: Optional[str] = SESSION_SUBJECT,
        *,
        key: Any = SESSION_KEY,
        kid: Optional[str] = SESSION_KID,
        algorithm: str = "ES256",
        lifetime: int = 300,
        **claims: Any,
) -> str:
    """An anonymous session token as the control plane signs it, valid for ``lifetime`` seconds from now."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": "aion",
        "aud": "aion-anonymous-session",
        "sub": subject,
        "subject_type": "AnonymousSession",
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
    }
    payload.update(claims)
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(
        {name: value for name, value in payload.items() if value is not None},
        key,
        algorithm=algorithm,
        headers=headers,
    )


def jwks_document(**keys: ec.EllipticCurvePublicKey) -> dict[str, Any]:
    """A JWKS document holding these public keys, by ``kid``."""
    return {
        "keys": [
            {**ECAlgorithm.to_jwk(public_key, as_dict=True), "kid": kid, "use": "sig"}
            for kid, public_key in keys.items()
        ]
    }


class ControlPlane:
    """A stand-in control plane serving a JWKS, whose document and availability a test changes."""

    def __init__(self, document: Optional[dict[str, Any]] = None) -> None:
        self.document = document if document is not None else jwks_document(
            **{SESSION_KID: SESSION_KEY.public_key()}
        )
        self.failing = False
        self.requests = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        assert str(request.url) == JWKS_URL
        if self.failing:
            return httpx.Response(503)
        return httpx.Response(200, json=self.document)

    def key_source(self, clock=time.monotonic) -> JwksKeySource:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return JwksKeySource(JWKS_URL, client=client, clock=clock)


class Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def platform_verifier() -> TokenVerifier:
    """A verifier that accepts only the stand-in platform's call tokens."""
    return TokenVerifier(call_keys=StaticKeySource(PLATFORM_KEY.public_key()), call_audience=CLIENT_ID)


def session_verifier(control_plane: Optional[ControlPlane] = None) -> TokenVerifier:
    """A verifier that accepts only anonymous session tokens the stand-in control plane signs."""
    return TokenVerifier(session_keys=(control_plane or ControlPlane()).key_source())


def open_verifier(control_plane: Optional[ControlPlane] = None) -> TokenVerifier:
    """A verifier that accepts both kinds, as a server outside the platform with credentials does."""
    return TokenVerifier(
        call_keys=StaticKeySource(PLATFORM_KEY.public_key()),
        call_audience=CLIENT_ID,
        session_keys=(control_plane or ControlPlane()).key_source(),
    )
