"""Whether the server requires a token, and what it verifies tokens with.

The one place the rule lives: with ``AION_CLIENT_ID`` and ``AION_CLIENT_SECRET``
the deployment has the platform behind it, and every protected request needs
the platform's bearer token; without them the server runs in local mode, with
authentication disabled. ``AppFactory``, the agent card and ``aion serve`` all
ask here rather than read the credentials themselves.
"""

from typing import Optional

from aion.core.settings import api_settings

from .platform import PlatformKeySource
from .verifier import TokenVerifier

__all__ = [
    "authentication_required",
    "build_token_verifier",
]


def authentication_required() -> bool:
    """Whether requests need the platform's bearer token; ``False`` is local mode."""
    return api_settings.has_credentials


def build_token_verifier() -> Optional[TokenVerifier]:
    """The verifier for the platform's tokens, or ``None`` in local mode."""
    if not authentication_required():
        return None
    return TokenVerifier(PlatformKeySource())
