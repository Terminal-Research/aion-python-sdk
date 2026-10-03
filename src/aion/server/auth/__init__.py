"""Who calls an agent: the bearer tokens requests carry, the keys they are verified with, and the caller they name.

Every protected request carries ``Authorization: Bearer <JWT>``.
``AionAuthMiddleware`` verifies it with a ``TokenVerifier`` and installs the
``AuthenticatedCaller`` the token names; its canonical ``sub`` is the owner of
the request's tasks and framework state. Two kinds of token are accepted, both
signed with the key Aion publishes (``JwksKeySource``): invocation tokens Aion
issues when it dispatches a request to this agent, and anonymous session tokens
a direct client holds. Outside the platform, a request without an Aion token
may be served as the user the application's own authentication installed.
Which of these a server accepts depends on where it runs;
``build_token_verifier`` is where that rule lives.
"""

from .caller import (
    Assurance,
    AuthenticatedCaller,
    CallerCredentials,
    CredentialKind,
    GatewayCoordinates,
    has_individual_access,
    verified_caller,
)
from .jwks import JwksKeySource
from .mode import AuthConfigurationError, build_token_verifier, deployment_id, is_hosted
from aion.core.principal import InvalidPrincipalError, Principal
from .verifier import (
    MAX_TOKEN_BYTES,
    InvalidTokenError,
    KeysUnavailableError,
    TokenClaim,
    TokenVerifier,
    claimed_kind,
)

__all__ = [
    "Assurance",
    "AuthConfigurationError",
    "AuthenticatedCaller",
    "CallerCredentials",
    "CredentialKind",
    "GatewayCoordinates",
    "InvalidPrincipalError",
    "InvalidTokenError",
    "JwksKeySource",
    "KeysUnavailableError",
    "MAX_TOKEN_BYTES",
    "Principal",
    "TokenClaim",
    "TokenVerifier",
    "build_token_verifier",
    "claimed_kind",
    "deployment_id",
    "has_individual_access",
    "is_hosted",
    "verified_caller",
]
