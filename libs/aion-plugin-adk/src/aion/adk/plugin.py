from typing import Optional, Any, override

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import AionLogger, get_logger
from aion.shared.plugins import AgentPluginProtocol

from .agent import ADKAdapter


class ADKPlugin(AgentPluginProtocol):
    """Agent Development Kit framework plugin."""

    NAME = "adk"

    def __init__(self):
        self._db_manager: Optional[DbManagerProtocol] = None
        self._adapter: Optional[ADKAdapter] = None
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
        self._adapter = ADKAdapter(db_manager=db_manager)

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
        if not self._adapter:
            raise RuntimeError(
                f"{self.name()} plugin not initialized. Call initialize() first."
            )
        return self._adapter

    @override
    async def health_check(self) -> bool:
        return self._adapter is not None


__all__ = ["ADKPlugin"]
