"""LangGraph plugin for AION framework."""

from typing import Any, Optional, override

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import AionLogger, get_logger
from aion.shared.plugins import AgentPluginProtocol

from .adapter import LangGraphAdapter


class LangGraphPlugin(AgentPluginProtocol):
    """LangGraph framework plugin."""

    NAME = "langgraph"

    def __init__(self):
        self._db_manager: Optional[DbManagerProtocol] = None
        self._adapter: Optional[LangGraphAdapter] = None
        self._logger: Optional[AionLogger] = None

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            self._logger = get_logger()
        return self._logger

    @override
    def name(self) -> str:
        return self.NAME

    @override
    async def initialize(self, db_manager: DbManagerProtocol, **deps: Any) -> None:
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
        if not self._adapter:
            raise RuntimeError(
                f"{self.name()} plugin not initialized. Call initialize() first."
            )
        return self._adapter

    @override
    async def health_check(self) -> bool:
        return self._adapter is not None


__all__ = ["LangGraphPlugin"]
