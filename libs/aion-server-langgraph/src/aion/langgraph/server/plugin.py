"""LangGraph plugin for AION framework."""

from typing import Any, Optional, override

from aion.core.db import DbManagerProtocol
from aion.core.logging import AionLogger
from aion.core.logging import get_logger
from aion.server.plugins import AgentPluginProtocol

from .adapter import LangGraphAdapter


class LangGraphPlugin(AgentPluginProtocol):
    """LangGraph framework plugin."""

    NAME = "langgraph"

    def __init__(self):
        """Set up uninitialized plugin state; call initialize() before use."""
        self._db_manager: Optional[DbManagerProtocol] = None
        self._adapter: Optional[LangGraphAdapter] = None
        self._logger: Optional[AionLogger] = None

    @property
    def logger(self) -> AionLogger:
        """Lazily initialize and return the plugin logger."""
        if not self._logger:
            self._logger = get_logger()
        return self._logger

    @override
    def name(self) -> str:
        """Return the plugin identifier used for registration."""
        return self.NAME

    @override
    async def initialize(self, db_manager: DbManagerProtocol, **deps: Any) -> None:
        """Wire up the db_manager and create the LangGraphAdapter instance."""
        self._db_manager = db_manager
        self._adapter = LangGraphAdapter(db_manager=db_manager)

    @override
    async def teardown(self) -> None:
        """Cleanup plugin resources.

        Currently no cleanup is needed as the adapter doesn't hold
        resources that need explicit cleanup.
        """
        self._adapter = None
        self._db_manager = None

    @override
    def get_adapter(self) -> LangGraphAdapter:
        """Return the initialized LangGraphAdapter; raises RuntimeError if not yet initialized."""
        if not self._adapter:
            raise RuntimeError(
                f"{self.name()} plugin not initialized. Call initialize() first."
            )
        return self._adapter

    @override
    async def health_check(self) -> bool:
        """Return True if the adapter has been initialized."""
        return self._adapter is not None


__all__ = ["LangGraphPlugin"]
