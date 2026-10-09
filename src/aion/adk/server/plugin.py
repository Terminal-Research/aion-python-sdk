"""AgentPluginProtocol implementation for the Google ADK framework."""

import logging
from typing import Optional, Any, override

from aion.core.db import DbManagerProtocol
from aion.core.logging import AionLogger
from aion.db.postgres.migrations import SchemaCheck
from aion.server.plugins import AgentPluginProtocol

from .adapter import ADKAdapter


class ADKPlugin(AgentPluginProtocol):
    """Agent Development Kit framework plugin."""

    NAME = "adk"

    def __init__(self):
        """Set up uninitialized plugin state; call initialize() before use."""
        self._db_manager: Optional[DbManagerProtocol] = None
        self._adapter: Optional[ADKAdapter] = None
        self._logger: Optional[AionLogger] = None

    @property
    def logger(self) -> AionLogger:
        """Lazily initialize and return the plugin logger."""
        if not self._logger:
            self._logger = logging.getLogger(__name__)
        return self._logger

    @override
    def name(self) -> str:
        """Return the plugin identifier used for registration."""
        return self.NAME

    @override
    async def initialize(
            self,
            db_manager: DbManagerProtocol,
            **deps: Any
    ) -> None:
        """Wire up the db_manager and create the ADKAdapter instance."""
        self._db_manager = db_manager
        self._adapter = ADKAdapter(db_manager=db_manager)

    @override
    async def migrate_database(self, db_manager: DbManagerProtocol) -> None:
        """Create google-adk's session tables in the session schema."""
        from .session.backends.postgres import migrate_session_tables

        await migrate_session_tables(db_manager)

    @override
    async def check_database(self, db_manager: DbManagerProtocol) -> Optional[SchemaCheck]:
        """Report whether google-adk's session tables exist."""
        from .session.backends.postgres import check_session_tables

        return await check_session_tables(db_manager)

    @override
    async def teardown(self) -> None:
        """Cleanup plugin resources.

        Currently no cleanup is needed as the adapter doesn't hold
        resources that need explicit cleanup.
        """
        self._adapter = None
        self._db_manager = None

    @override
    def get_adapter(self) -> ADKAdapter:
        """Return the initialized ADKAdapter; raises RuntimeError if not yet initialized."""
        if not self._adapter:
            raise RuntimeError(
                f"{self.name()} plugin not initialized. Call initialize() first."
            )
        return self._adapter

    @override
    async def health_check(self) -> bool:
        """Return True if the adapter has been initialized."""
        return self._adapter is not None


__all__ = ["ADKPlugin"]
