"""The key the platform signs this deployment's request tokens with."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from aion.api.http import aion_jwt_manager

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric import ec

logger = logging.getLogger(__name__)

__all__ = [
    "PlatformKeySource",
]


class PlatformKeySource:
    """The platform's public key for this deployment, fetched at startup.

    The deployment first buys its own token with ``AION_CLIENT_ID`` and
    ``AION_CLIENT_SECRET``, then asks the control plane for the key in a
    separate request. Every client id has a key of its own, so a token the
    platform signed for another deployment does not verify here.
    """

    def __init__(self, jwt_manager: Any = aion_jwt_manager) -> None:
        self._jwt_manager = jwt_manager
        self._key: Optional[ec.EllipticCurvePublicKey] = None

    async def load(self) -> None:
        """Fetch the key; without one, every request the platform signs is refused."""
        token = await self._jwt_manager.get_token()
        if token is None:
            logger.warning(
                "No deployment token from AION_CLIENT_ID / AION_CLIENT_SECRET, so the "
                "platform's verification key cannot be fetched: requests the platform "
                "signs will be refused")
            return
        self._key = await self._fetch_key(token)
        if self._key is None:
            logger.warning(
                "The platform's verification key is not available, so requests the "
                "platform signs will be refused")

    def current_key(self) -> Optional[ec.EllipticCurvePublicKey]:
        return self._key

    async def _fetch_key(self, token: Any) -> Optional[ec.EllipticCurvePublicKey]:
        """Ask the control plane for the key, authorized by the deployment's token."""
        # TODO: request the key from the control plane - most likely a GraphQL
        #  query authorized by `token` - once the platform exposes it.
        return None
