"""A stand-in for Aion's key endpoint, and verifiers built against it.

Tokens are signed by ``tests.support.aion_tokens``; this module serves the key
they verify with through an ``httpx.MockTransport``, whose document and
availability a test changes.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from aion.server.auth import JwksKeySource, TokenVerifier
from tests.support.aion_tokens import CLIENT_ID, ISSUER, SigningKey

AION_KEY = SigningKey()
"""The key the stand-in Aion signs every token with."""

JWKS_URL = "https://api.aion.example/runtime/a2a/verification-keys"


class ControlPlane:
    """A stand-in key endpoint serving a JWKS, whose document and availability a test changes."""

    def __init__(self, document: Optional[dict[str, Any]] = None) -> None:
        self.document = document if document is not None else AION_KEY.jwks()
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


def verifier(
        control_plane: Optional[ControlPlane] = None,
        *,
        invocations: bool = True,
        sessions: bool = True,
) -> TokenVerifier:
    """A verifier over the stand-in endpoint.

    By default it accepts both kinds and the application's users, as a server
    outside the platform with ``AION_CLIENT_ID`` does. One without sessions
    takes invocation tokens only, as a hosted or strict server does.
    """
    return TokenVerifier(
        (control_plane or ControlPlane()).key_source(),
        issuer=ISSUER,
        invocation_audience=CLIENT_ID if invocations else None,
        accept_sessions=sessions,
        trust_application_users=sessions,
    )


def hosted_verifier(control_plane: Optional[ControlPlane] = None) -> TokenVerifier:
    """A verifier that accepts invocation tokens only, as a hosted or strict server does."""
    return verifier(control_plane, sessions=False)


def session_verifier(control_plane: Optional[ControlPlane] = None) -> TokenVerifier:
    """A verifier that accepts anonymous session tokens only, as a server without ``AION_CLIENT_ID`` does."""
    return verifier(control_plane, invocations=False)
