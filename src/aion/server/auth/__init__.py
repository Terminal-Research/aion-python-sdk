"""Who calls an agent: the platform's bearer tokens, the key they are verified with, and the caller they name.

With ``AION_CLIENT_ID`` and ``AION_CLIENT_SECRET`` set, every protected request
carries ``Authorization: Bearer <JWT>``. ``AionAuthMiddleware`` verifies it with
a ``TokenVerifier`` against the platform's key (``PlatformKeySource``) and
installs the ``AuthenticatedCaller`` the token names; its ``sub`` is the owner
of the request's tasks and framework state. Without the credentials the server
runs in local mode, with authentication disabled. ``authentication_required``
and ``build_token_verifier`` are where that rule lives.
"""

from .caller import AuthenticatedCaller
from .mode import authentication_required, build_token_verifier
from .platform import PlatformKeySource
from .verifier import InvalidTokenError, KeySource, TokenVerifier

__all__ = [
    "AuthenticatedCaller",
    "InvalidTokenError",
    "KeySource",
    "PlatformKeySource",
    "TokenVerifier",
    "authentication_required",
    "build_token_verifier",
]
