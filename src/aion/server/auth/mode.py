"""Which tokens the server accepts, decided from where it runs.

Every protected request carries a bearer token; there is no mode without one.
What differs is which kinds ``TokenVerifier`` accepts. A server the Aion
platform deployed accepts only the platform's call tokens. Anywhere else - a
remote server or local development - it also accepts anonymous session tokens,
and call tokens only when ``AION_CLIENT_ID`` and ``AION_CLIENT_SECRET`` say
the deployment has an identity on the platform. ``AppFactory`` asks
``build_token_verifier``; nothing else reads these settings.
"""

import logging
import os

from aion.core.settings import api_settings

from .jwks import JwksKeySource
from .platform import PlatformKeySource
from .verifier import TokenVerifier

logger = logging.getLogger(__name__)

__all__ = [
    "build_token_verifier",
    "is_hosted",
]


def is_hosted() -> bool:
    """Whether the Aion platform deployed this server."""
    # Set by the platform in the environment of the servers it deploys. Not an
    # AION_* setting and not documented for users: they cannot opt in.
    return bool(os.environ.get("DEPLOYMENT_ID", "").strip())


def build_token_verifier() -> TokenVerifier:
    """The verifier for the tokens this server accepts in the mode it runs in."""
    hosted = is_hosted()
    accepts_call_tokens = hosted or api_settings.has_credentials
    if hosted:
        logger.info("Authentication: hosted on the Aion platform; accepting the platform's call tokens")
    elif accepts_call_tokens:
        logger.info("Authentication: not hosted; accepting the platform's call tokens and anonymous session tokens")
    else:
        logger.info("Authentication: not hosted, no platform credentials; accepting anonymous session tokens only")
    return TokenVerifier(
        call_keys=PlatformKeySource() if accepts_call_tokens else None,
        call_audience=api_settings.client_id if accepts_call_tokens else None,
        session_keys=None if hosted else JwksKeySource(api_settings.verification_keys_url),
    )
