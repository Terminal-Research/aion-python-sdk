"""Request tokens as Aion signs them under contract A, from a stand-in signing key.

Every suite that sends a request through ``AionAuthMiddleware`` signs its
tokens here, so the unit, integration and scenario suites agree on one wire
contract: ES256, a ``kid`` that is the key's RFC 7638 thumbprint, a typed
header, and the claims ``aion.server.auth.TokenVerifier`` requires. A claim
or header set to ``None`` is left out, which is how a test removes one.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any, Optional

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from aion.server.auth import Principal
from aion.server.auth.jwks import jwk_thumbprint

__all__ = [
    "CLIENT_ID",
    "EDGE_ENVIRONMENT_ID",
    "ISSUER",
    "OWNER_AGENT_IDENTITY_ID",
    "SigningKey",
    "TERMINAL_ENVIRONMENT_ID",
    "subject",
]

ISSUER = "aion.io"
"""The issuer Aion signs as by default, and ``AION_API_CLIENT_AUTH_ISSUER``'s default."""

CLIENT_ID = "client-123"
"""The ``AION_CLIENT_ID`` of the server under test: the audience of its invocation tokens."""

SESSION_AUDIENCE = "urn:aion:a2a:anonymous-session"

OWNER_AGENT_IDENTITY_ID = "6f1c2a40-0d5e-4b8e-9a51-3c1f2e7d9b10"
EDGE_ENVIRONMENT_ID = "0b7e4f3a-62d1-4c59-8f0e-a2c9d14b7e21"
TERMINAL_ENVIRONMENT_ID = "9d3a1c75-4e8b-42f6-b0d7-5e6f8a1c2b34"

INVOCATION_LIFETIME = 3600
SESSION_LIFETIME = 30 * 24 * 3600


def subject(principal_type: str, principal_id: str) -> str:
    """The canonical subject of a principal."""
    return Principal(principal_type, principal_id).subject


class SigningKey:
    """A P-256 key that signs tokens the way Aion does, and the JWKS that publishes it."""

    def __init__(self, key: Optional[ec.EllipticCurvePrivateKey] = None) -> None:
        self.key = key or ec.generate_private_key(ec.SECP256R1())
        self.kid = jwk_thumbprint(self.public_jwk(with_kid=False))

    def public_jwk(self, *, with_kid: bool = True, **members: Any) -> dict[str, Any]:
        """The published form of the public key."""
        jwk = ECAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        if with_kid:
            jwk.update({"kid": self.kid, "alg": "ES256", "use": "sig"})
        jwk.update(members)
        return {name: value for name, value in jwk.items() if value is not None}

    def jwks(self) -> dict[str, Any]:
        """A JWKS publishing this key alone."""
        return {"keys": [self.public_jwk()]}

    def invocation_token(
            self,
            principal_id: str = "7a9e2b1c-1111-4d2e-8f3a-6b5c4d3e2f10",
            *,
            principal_type: str = "AionUser",
            assurance: str = "account",
            audience: Any = CLIENT_ID,
            issued_at: Optional[int] = None,
            headers: Optional[dict[str, Any]] = None,
            **claims: Any,
    ) -> str:
        """An invocation token for this principal, issued ``issued_at`` (now by default)."""
        now = int(time.time()) if issued_at is None else issued_at
        payload = {
            "iss": ISSUER,
            "aud": audience,
            "sub": subject(principal_type, principal_id),
            "token_use": "a2a_invocation",
            "contract_version": 1,
            "assurance": assurance,
            "owner_agent_identity_id": OWNER_AGENT_IDENTITY_ID,
            "edge_agent_environment_id": EDGE_ENVIRONMENT_ID,
            "terminal_agent_environment_id": TERMINAL_ENVIRONMENT_ID,
            "iat": now,
            "nbf": now,
            "exp": now + INVOCATION_LIFETIME,
            **claims,
        }
        return self.sign(payload, typ="aion-invocation+jwt", headers=headers)

    def session_token(
            self,
            session_id: Optional[str] = None,
            *,
            issued_at: Optional[int] = None,
            headers: Optional[dict[str, Any]] = None,
            **claims: Any,
    ) -> str:
        """An anonymous session token; a fresh session unless ``session_id`` names one."""
        now = int(time.time()) if issued_at is None else issued_at
        payload = {
            "iss": ISSUER,
            "aud": SESSION_AUDIENCE,
            "sub": subject("AnonymousSession", session_id or str(uuid.uuid4())),
            "token_use": "anonymous_session",
            "contract_version": 1,
            "assurance": "session",
            "iat": now,
            "nbf": now,
            "exp": now + SESSION_LIFETIME,
            **claims,
        }
        return self.sign(payload, typ="aion-session+jwt", headers=headers)

    def sign(self, payload: dict[str, Any], *, typ: Optional[str], headers: Optional[dict[str, Any]] = None) -> str:
        """``payload`` signed ES256 under this key's ``kid``, with ``headers`` added or (``None``) removed."""
        protected = {"alg": "ES256", "typ": typ, "kid": self.kid, **(headers or {})}
        protected = {name: value for name, value in protected.items() if value is not None}
        payload = {name: value for name, value in payload.items() if value is not None}
        return jwt.encode(payload, self.key, algorithm="ES256", headers=protected)

    def sign_raw(self, header_json: str, payload_json: str) -> str:
        """A compact JWS of exactly these JSON texts, for members a dict cannot repeat."""
        signing_input = f"{_b64(header_json.encode())}.{_b64(payload_json.encode())}"
        signature = jwt.algorithms.ECAlgorithm(jwt.algorithms.ECAlgorithm.SHA256).sign(
            signing_input.encode(), self.key
        )
        return f"{signing_input}.{_b64(signature)}"

    def header_json(self, typ: str = "aion-invocation+jwt") -> str:
        return json.dumps({"alg": "ES256", "typ": typ, "kid": self.kid})


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
