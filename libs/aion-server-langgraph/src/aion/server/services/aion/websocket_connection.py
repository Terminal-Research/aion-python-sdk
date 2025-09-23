from aion.server.core.base import BaseService
from aion.server.interfaces import IWebSocketManager


class AionWebSocketService(BaseService):
    """Service wrapper for managing WebSocket connections with dependency injection."""

    def __init__(self, websocket_manager: IWebSocketManager, **kwargs) -> None:
        """
        Initialize the WebSocket service.

        Args:
            websocket_manager: WebSocket manager instance to control connections.
            logger: Optional logger instance.
        """
        super().__init__(**kwargs)
        self.websocket_manager = websocket_manager

    async def start_connection(self) -> None:
        """Start WebSocket connection with Aion API."""
        try:
            await self.websocket_manager.start()
        except Exception as ex:
            self.logger.error("Failed to start websocket connection to aion platform: %s", ex)

    async def stop_connection(self) -> None:
        """Stop WebSocket connection with Aion API."""
        try:
            await self.websocket_manager.stop()
        except Exception as ex:
            self.logger.error("Failed to stop websocket connection: %s", ex)
