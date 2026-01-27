"""Service for preparing CLI execution environment before starting agents."""
import os
from dataclasses import dataclass
from typing import Optional

from aion.api.gql import AionGqlContextClient
from aion.shared.services import BaseExecuteService
from aion.shared.settings import app_settings


@dataclass
class EnvironmentContext:
    """Environment context with VERSION_ID and its source."""
    version_id: Optional[str] = None


class ServeEnvironmentPreparerService(BaseExecuteService):
    """Prepares environment before starting agents (VERSION_ID, etc)."""

    async def execute(self) -> EnvironmentContext:
        """Prepare environment by ensuring VERSION_ID is available."""
        if version_id := os.environ.get('VERSION_ID'):
            self.logger.debug(f"VERSION_ID found in environment: {version_id}")
            return EnvironmentContext(version_id=version_id)

        version_id = await self._fetch_version_from_control_plane()

        if version_id:
            self._cache_version_id(version_id)
            self.logger.debug(f"VERSION_ID obtained from control plane: {version_id}")
            return EnvironmentContext(version_id=version_id)

        self.logger.warning("VERSION_ID not available from env or control plane")
        return EnvironmentContext(version_id=None)

    async def _fetch_version_from_control_plane(self) -> Optional[str]:
        """Fetch VERSION_ID from control plane via GraphQL."""
        # TODO: replace with an implementation using the Aion GraphQL client. Currently returns mock data.
        return "6b2a5b4e-d0f5-48d3-a913-f8b77f4"
        # try:
        #     async with AionGqlContextClient() as client:
        #         return await client.get_current_deployment_version()
        # except Exception as ex:
        #     self.logger.warning(f"Failed to fetch VERSION_ID from control plane: {ex}")
        #     return None

    @staticmethod
    def _cache_version_id(version_id: str) -> None:
        """Cache VERSION_ID in os.environ (for child processes) and app_settings (for parent)."""
        os.environ['VERSION_ID'] = version_id
        app_settings.version_id = version_id
