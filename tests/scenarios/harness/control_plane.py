"""A stand-in Aion control plane: the key set a server verifies anonymous sessions with, and the tokens signed by it.

Every server the scenarios start is pointed at this one (``AION_API_HOST``), and
every client presents a token it signs, so the scenarios drive the same
authentication a deployment outside the platform runs, end to end over real
sockets.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

__all__ = [
    "DEFAULT_SUBJECT",
    "FakeControlPlane",
    "control_plane",
]

VERIFICATION_KEYS_PATH = "/runtime/a2a/verification-keys"

DEFAULT_SUBJECT = "aion:anonymous:scenarios"
"""The session a client belongs to unless a scenario names another."""

_KID = "scenarios-key-1"
_TOKEN_LIFETIME_SECONDS = 6 * 60 * 60
"""Longer than any run: a session-scoped client signs its token once."""


class FakeControlPlane:
    """Serves a JWKS on a local port and signs anonymous session tokens with its key."""

    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        self._document = json.dumps(
            {"keys": [{**ECAlgorithm.to_jwk(self._key.public_key(), as_dict=True), "kid": _KID, "use": "sig"}]}
        ).encode()
        document = self._document

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name http.server dispatches on
                if self.path != VERIFICATION_KEYS_PATH:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(document)))
                self.end_headers()
                self.wfile.write(document)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        """The value of ``AION_API_HOST`` that points a server here."""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def token(self, subject: str = DEFAULT_SUBJECT, **claims: Any) -> str:
        """An anonymous session token for ``subject``, signed by this control plane."""
        now = int(time.time())
        payload = {
            "iss": "aion",
            "aud": "aion-anonymous-session",
            "sub": subject,
            "subject_type": "AnonymousSession",
            "iat": now,
            "nbf": now,
            "exp": now + _TOKEN_LIFETIME_SECONDS,
            **claims,
        }
        return jwt.encode(
            {name: value for name, value in payload.items() if value is not None},
            self._key,
            algorithm="ES256",
            headers={"kid": _KID},
        )

    def forged_token(self, subject: str = DEFAULT_SUBJECT) -> str:
        """A token that claims this control plane's key but is signed with another."""
        now = int(time.time())
        return jwt.encode(
            {
                "iss": "aion",
                "aud": "aion-anonymous-session",
                "sub": subject,
                "subject_type": "AnonymousSession",
                "exp": now + 300,
            },
            ec.generate_private_key(ec.SECP256R1()),
            algorithm="ES256",
            headers={"kid": _KID},
        )

    def close(self) -> None:
        """Stop serving."""
        self._server.shutdown()
        self._server.server_close()


_INSTANCE: Optional[FakeControlPlane] = None
_LOCK = threading.Lock()


def control_plane() -> FakeControlPlane:
    """The control plane of this session, started on first use."""
    global _INSTANCE
    with _LOCK:
        if _INSTANCE is None:
            _INSTANCE = FakeControlPlane()
        return _INSTANCE
