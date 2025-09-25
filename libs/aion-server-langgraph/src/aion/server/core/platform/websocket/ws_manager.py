import asyncio
from typing import Optional

from aion.shared.logging import get_logger

from aion.server.interfaces import IWebsocketTransportFactory, IWebSocketManager

logger = get_logger(__name__)

__all__ = [
    "AionWebSocketManager",
]


class AionWebSocketManager(IWebSocketManager):
    """
    WebSocket connection manager for Aion platform with dependency injection.

    Provides basic agent communication with the Aion ecosystem through
    persistent WebSocket connection with automatic reconnection.
    """

    def __init__(
            self,
            ws_transport_factory: IWebsocketTransportFactory,
            ping_interval: float = 30.0,
            reconnect_delay: float = 10.0,
    ):
        """
        Initialize WebSocket manager with injected dependencies.

        Args:
            ws_transport_factory: Factory for creating WebSocket transports
            ping_interval: Interval between ping messages (seconds)
            reconnect_delay: Delay before reconnection attempt (seconds)
        """
        self._ws_transport_factory = ws_transport_factory
        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay

        self._websocket_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._connection_ready = asyncio.Event()
        self._transport: Optional = None

    async def start(self) -> None:
        """Start WebSocket connection with the platform."""
        try:
            self._websocket_task = asyncio.create_task(self._connection_loop())

            await asyncio.wait_for(self._connection_ready.wait(), timeout=30.0)
            logger.info("Connection to Aion platform established")

        except asyncio.TimeoutError:
            logger.error("Timeout while connecting to Aion platform")
            await self.stop()
            raise ConnectionError("Failed to establish connection to Aion platform")
        except Exception as e:
            logger.error(f"Error starting WebSocket manager: {e}")
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop WebSocket connection."""
        logger.info("Stopping connection to Aion platform...")

        self._shutdown_event.set()

        if self._websocket_task and not self._websocket_task.done():
            try:
                await asyncio.wait_for(self._websocket_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("WebSocket stop timeout, forcing cancellation...")
                self._websocket_task.cancel()
                try:
                    await self._websocket_task
                except asyncio.CancelledError:
                    pass

        logger.info("Connection to Aion platform closed")

    async def _connection_loop(self) -> None:
        """Main connection maintenance loop with reconnection."""
        first_connection = True

        while not self._shutdown_event.is_set():
            try:
                await self._establish_connection()

                if first_connection:
                    self._connection_ready.set()
                    first_connection = False
                    logger.info("First connection established, signaling ready")

                await self._run_ping_loop()

            except KeyboardInterrupt:
                logger.info("Connection stopped by user request")
                break
            except Exception as ex:
                if first_connection:
                    logger.error(f"Websocket connection startup failed: {ex}")
                    break

                if not self._shutdown_event.is_set():
                    logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=self.reconnect_delay
                        )
                    except asyncio.TimeoutError:
                        self._connection_ready.clear()
                        continue
                    break
                else:
                    logger.error(f"WebSocket connection error: {ex}")

    async def _establish_connection(self) -> None:
        """Establish connection without blocking on pings."""
        self._transport = await self._ws_transport_factory.create_transport()
        await self._transport.connect()
        logger.info("WebSocket connection established")

    async def _run_ping_loop(self) -> None:
        """Run the ping loop to keep connection alive."""
        try:
            ping_count = 0
            while not self._shutdown_event.is_set():
                await asyncio.sleep(self.ping_interval)

                try:
                    if hasattr(self._transport, 'websocket') and self._transport.websocket:
                        await self._transport.websocket.ping()
                        ping_count += 1
                        logger.debug(f"Ping #{ping_count} sent")
                    else:
                        logger.warning("WebSocket not available for ping")
                        break
                except Exception as e:
                    logger.error(f"Ping failed: {e}")
                    break

        finally:
            if self._transport:
                await self._transport.close()
                logger.info("WebSocket connection closed")

    @property
    def is_connected(self) -> bool:
        """
        Check if connection to the platform is active.

        Returns:
            True if connection is active, False otherwise
        """
        return (
                self._websocket_task and
                not self._websocket_task.done() and
                not self._shutdown_event.is_set() and
                self._connection_ready.is_set()
        )
