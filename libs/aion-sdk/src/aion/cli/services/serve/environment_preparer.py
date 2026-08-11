"""Service for preparing CLI execution environment before starting agents."""
import os
from dataclasses import dataclass
from typing import Optional

from aion.api.gql import AionGqlContextClient
from aion.api.http import aion_jwt_manager
from aion.core.settings import api_settings
from aion.server.services import BaseExecuteService
from aion.server.settings import app_settings


@dataclass
class EnvironmentContext:
    """Environment context with VERSION_ID and its source."""
    version_id: Optional[str] = None
    auth_available: bool = False


class ServeEnvironmentPreparerService(BaseExecuteService):
    """Prepares environment before starting agents (auth state, VERSION_ID, etc)."""

    async def execute(self) -> EnvironmentContext:
        """Prepare environment by settling platform auth and VERSION_ID.

        A deployment identifies itself to the control plane with its credentials,
        so without them there is no version to look up and nothing to warn about:
        the query would fail on its own missing arguments and report a local run as
        two failures of the platform.
        """
        auth_available = await self._preflight_auth()

        if version_id := os.environ.get('VERSION_ID'):
            self.logger.debug(f"VERSION_ID found in environment: {version_id}")
            return EnvironmentContext(version_id=version_id, auth_available=auth_available)

        if not api_settings.has_credentials:
            self.logger.debug(
                "No Aion credentials, serving without a VERSION_ID")
            return EnvironmentContext(version_id=None, auth_available=auth_available)

        version_id = await self._fetch_version_from_control_plane()

        if version_id:
            self._cache_version_id(version_id)
            self.logger.debug(f"VERSION_ID obtained from control plane: {version_id}")
            return EnvironmentContext(version_id=version_id, auth_available=auth_available)

        self.logger.warning("VERSION_ID not available from env or control plane")
        return EnvironmentContext(version_id=None, auth_available=auth_available)

    @staticmethod
    async def _preflight_auth() -> bool:
        """Settle platform auth once, in the parent, before any agent is forked.

        Agents are started with the ``fork`` start method, so whatever the
        shared ``aion_jwt_manager`` holds at this point is what every child
        begins with: a valid token they can reuse instead of each fetching
        their own, or a latched 401 that keeps them from each re-reporting the
        same failure. Without this, the parent and every agent discover a bad
        credential independently and log the same warning N+1 times.
        """
        return (await aion_jwt_manager.get_token()) is not None

    async def _fetch_version_from_control_plane(self) -> Optional[str]:
        """Fetch VERSION_ID from control plane via GraphQL."""
        try:
            async with AionGqlContextClient() as client:
                return await client.get_current_deployment_version()
        except Exception as ex:
            self.logger.warning(f"Failed to fetch VERSION_ID from control plane: {ex}")
            return None

    @staticmethod
    def _cache_version_id(version_id: str) -> None:
        """Cache VERSION_ID in os.environ (for child processes) and app_settings (for parent)."""
        os.environ['VERSION_ID'] = version_id
        app_settings.version_id = version_id
