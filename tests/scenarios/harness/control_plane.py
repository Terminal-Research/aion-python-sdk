"""A stand-in Aion control plane: the key set a server verifies tokens with, and the tokens signed by it.

Every server the scenarios start is pointed at this one (``AION_API_HOST``), and
every client presents a token it signs, so the scenarios drive the same
authentication a deployment outside the platform runs, end to end over real
sockets. Tokens follow contract A (``tests.support.aion_tokens``).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from aion.server.auth import Principal
from tests.support.aion_tokens import CLIENT_ID, SigningKey, subject

__all__ = [
    "CLIENT_ID",
    "DEFAULT_SUBJECT",
    "FakeControlPlane",
    "control_plane",
    "session_subject",
]

VERIFICATION_KEYS_PATH = "/runtime/a2a/verification-keys"


def session_subject(session_id: str) -> str:
    """The canonical subject of the anonymous session ``session_id``, a lowercase UUID."""
    return subject("AnonymousSession", session_id)


DEFAULT_SUBJECT = session_subject("5c0e9a52-3b7d-4f18-9a26-0d4e8b1c7f35")
"""The session a client belongs to unless a scenario names another."""


class FakeControlPlane:
    """Serves a JWKS on a local port and signs anonymous session and invocation tokens with its key."""

    def __init__(self) -> None:
        self.signer = SigningKey()
        document = json.dumps(self.signer.jwks()).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name http.server dispatches on
                if self.path != VERIFICATION_KEYS_PATH:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/jwk-set+json")
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

    def token(self, session: str = DEFAULT_SUBJECT, **claims: Any) -> str:
        """An anonymous session token for the session subject ``session``, signed by this control plane.

        A token signed now lives the contract's 30 days, longer than any run,
        so a session-scoped client signs its token once.
        """
        return self.signer.session_token(Principal.from_subject(session).id, **claims)

    def invocation_token(self, principal_id: str = "scenario-user", **claims: Any) -> str:
        """An invocation token Aion would send a deployment whose ``AION_CLIENT_ID`` is ``CLIENT_ID``."""
        return self.signer.invocation_token(principal_id, **claims)

    def forged_token(self, session: str = DEFAULT_SUBJECT) -> str:
        """A token that claims this control plane's key but is signed with another."""
        forger = SigningKey()
        forger.kid = self.signer.kid
        return forger.session_token(Principal.from_subject(session).id)

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
