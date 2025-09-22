from typing import Optional

from aion.api.http import AionJWTManager

from aion.server.core.base import BaseExecuteService


class AionGetAuthTokenService(BaseExecuteService):
    """
    Service for retrieving authentication tokens using JWT manager.

    This service wraps the JWT manager's token retrieval functionality
    and provides it through the base service execution pattern.
    """

    def __init__(self, jwt_manager: AionJWTManager, **kwargs):
        super().__init__(**kwargs)
        self.jwt_manager = jwt_manager

    async def execute(self) -> Optional[str]:
        """
        Execute the service to retrieve an authentication token.

        Returns:
            Optional[str]: The authentication token if available, None otherwise.
        """
        return await self.jwt_manager.get_token()
