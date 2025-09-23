from aion.api.http import AionJWTManager

from aion.server.core.base import BaseService
from aion.server.interfaces import IAuthManager


class AionAuthManagerService(BaseService, IAuthManager):
    """Service implementation for JWT-based authentication management."""

    def __init__(self, jwt_manager: AionJWTManager, **kwargs):
        """Initialize the auth manager service.

        Args:
            jwt_manager: JWT manager instance for token operations.
        """
        super().__init__(**kwargs)
        self.jwt_manager = jwt_manager

    async def get_token(self) -> str:
        """Get authentication token from JWT manager.

        Returns:
            JWT authentication token.
        """
        return await self.jwt_manager.get_token()
