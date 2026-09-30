"""Who calls an agent: the bearer tokens requests carry, the keys they are verified with, and the caller they name.

Every protected request carries ``Authorization: Bearer <JWT>``.
``AionAuthMiddleware`` verifies it with a ``TokenVerifier`` and installs the
``AuthenticatedCaller`` the token names; its ``sub`` is the owner of the
request's tasks and framework state. Two kinds of token are accepted: call
tokens Aion signs for this deployment (``PlatformKeySource``) and anonymous
session tokens signed with keys the control plane publishes
(``JwksKeySource``). Which kinds a server accepts depends on where it runs;
``build_token_verifier`` is where that rule lives.
"""

from .caller import AuthenticatedCaller
from .jwks import JwksKeySource
from .mode import build_token_verifier, is_hosted
from .platform import PlatformKeySource
from .verifier import InvalidTokenError, KeySource, TokenVerifier

__all__ = [
    "AuthenticatedCaller",
    "InvalidTokenError",
    "JwksKeySource",
    "KeySource",
    "PlatformKeySource",
    "TokenVerifier",
    "build_token_verifier",
    "is_hosted",
]
