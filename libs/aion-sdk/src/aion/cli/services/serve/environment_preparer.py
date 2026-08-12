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


class ServeEnvironmentPreparerService(BaseExecuteService):
    """Prepares environment before starting agents (VERSION_ID, etc)."""

    async def execute(self) -> EnvironmentContext:
        """Prepare environment by ensuring VERSION_ID is available.

        A deployment identifies itself to the control plane with its credentials,
        so without them there is no version to look up and nothing to warn about:
        the query would fail on its own missing arguments and report a local run as
        two failures of the platform.
        """
        if version_id := os.environ.get('VERSION_ID'):
            self.logger.debug(f"VERSION_ID found in environment: {version_id}")
            return EnvironmentContext(version_id=version_id)

        if not api_settings.has_credentials:
            self.logger.debug(
                "No Aion credentials, serving without a VERSION_ID")
            return EnvironmentContext(version_id=None)

        if version_id := await self._version_from_token():
            self._cache_version_id(version_id)
            self.logger.debug(f"VERSION_ID obtained from access token: {version_id}")
            return EnvironmentContext(version_id=version_id)

        version_id = await self._fetch_version_from_control_plane()

        if version_id:
            self._cache_version_id(version_id)
            self.logger.debug(f"VERSION_ID obtained from control plane: {version_id}")
            return EnvironmentContext(version_id=version_id)

        self.logger.warning("VERSION_ID not available from env, token, or control plane")
        return EnvironmentContext(version_id=None)

    async def _version_from_token(self) -> Optional[str]:
        """Read VERSION_ID out of the access token the credentials already bought.

        Client credentials are issued per version, so the token that authenticates
        the control-plane query carries in its ``sub`` the exact id that query
        returns - the round trip only asks the server to repeat what it just said.

        A token scoped to anything but a version yields ``None`` and the caller
        falls back to the query, which resolves the version by client id and is
        correct for those principals too.
        """
        try:
            return await aion_jwt_manager.get_version_id()
        except Exception as ex:
            self.logger.warning(f"Failed to read VERSION_ID from access token: {ex}")
            return None

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
