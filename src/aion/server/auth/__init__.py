"""Who calls an agent: the bearer tokens requests carry, the keys they are verified with, and the caller they name.

Every protected request carries ``Authorization: Bearer <JWT>``.
``AionAuthMiddleware`` verifies it with a ``TokenVerifier`` and installs the
``AuthenticatedCaller`` the token names; its canonical ``sub`` is the owner of
the request's tasks and framework state. Two kinds of token are accepted, both
signed with the key Aion publishes (``JwksKeySource``): invocation tokens Aion
issues when it dispatches a request to this agent, and anonymous session tokens
a direct client holds. Which kinds a server accepts depends on where it runs;
``build_token_verifier`` is where that rule lives.
"""

from .caller import Assurance, AuthenticatedCaller, CredentialKind, GatewayCoordinates
from .jwks import JwksKeySource
from .mode import AuthConfigurationError, build_token_verifier, deployment_id, is_hosted
from .principal import InvalidPrincipalError, Principal
from .verifier import InvalidTokenError, KeysUnavailableError, TokenVerifier

__all__ = [
    "Assurance",
    "AuthConfigurationError",
    "AuthenticatedCaller",
    "CredentialKind",
    "GatewayCoordinates",
    "InvalidPrincipalError",
    "InvalidTokenError",
    "JwksKeySource",
    "KeysUnavailableError",
    "Principal",
    "TokenVerifier",
    "build_token_verifier",
    "deployment_id",
    "is_hosted",
]
