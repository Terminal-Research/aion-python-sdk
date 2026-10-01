"""Which tokens the server accepts, decided from where it runs.

Every protected request carries a bearer token; there is no mode without one.
What differs is which kinds ``TokenVerifier`` accepts. A server the Aion
platform deployed - ``DEPLOYMENT_ID`` names its deployment - accepts only
invocation tokens addressed to its ``AION_CLIENT_ID``. Anywhere else it
accepts anonymous session tokens, and invocation tokens too when
``AION_CLIENT_ID`` is set. Both are verified with the one key set Aion
publishes at ``{AION_API_HOST}/runtime/a2a/verification-keys``.
``AppFactory`` asks ``build_token_verifier``; nothing else reads these settings.
"""

import logging
import os
import uuid
from typing import Optional

from aion.core.exceptions import AionError
from aion.core.settings import api_settings

from .jwks import JwksKeySource
from .verifier import TokenVerifier

logger = logging.getLogger(__name__)

__all__ = [
    "AuthConfigurationError",
    "build_token_verifier",
    "deployment_id",
    "is_hosted",
]


class AuthConfigurationError(AionError):
    """The settings authentication depends on are wrong; the server does not start."""


def deployment_id() -> Optional[str]:
    """The deployment the Aion platform runs this server as; ``None`` when it does not.

    Raises:
        AuthConfigurationError: ``DEPLOYMENT_ID`` is set but is not a UUID. A
            malformed or empty value never means "not hosted".
    """
    value = os.environ.get("DEPLOYMENT_ID")
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise AuthConfigurationError(
            "DEPLOYMENT_ID is set but is not a deployment UUID; a server the Aion platform hosts "
            "needs its real deployment ID, and any other server must leave it unset"
        ) from None


def is_hosted() -> bool:
    """Whether the Aion platform deployed this server.

    Raises:
        AuthConfigurationError: ``DEPLOYMENT_ID`` is malformed.
    """
    return deployment_id() is not None


def build_token_verifier() -> TokenVerifier:
    """The verifier for the tokens this server accepts in the mode it runs in.

    Raises:
        AuthConfigurationError: The settings cannot make a safe verifier: a
            malformed ``DEPLOYMENT_ID``, a hosted server without
            ``AION_CLIENT_ID``, or keys at an untrusted URL.
    """
    hosted = is_hosted()
    client_id = api_settings.client_id or None
    if hosted and client_id is None:
        raise AuthConfigurationError(
            "DEPLOYMENT_ID is set, so invocation tokens are required, but AION_CLIENT_ID is not: "
            "nothing could be verified as addressed to this server"
        )
    try:
        keys = JwksKeySource(api_settings.verification_keys_url)
    except ValueError as error:
        raise AuthConfigurationError(str(error)) from None

    if hosted:
        logger.info("Authentication: hosted on the Aion platform; accepting invocation tokens only")
    elif client_id is not None:
        logger.info("Authentication: not hosted; accepting invocation tokens and anonymous session tokens")
    else:
        logger.info("Authentication: not hosted, no AION_CLIENT_ID; accepting anonymous session tokens only")
    return TokenVerifier(
        keys,
        issuer=api_settings.client_auth_issuer,
        invocation_audience=client_id,
        accept_sessions=not hosted,
    )
