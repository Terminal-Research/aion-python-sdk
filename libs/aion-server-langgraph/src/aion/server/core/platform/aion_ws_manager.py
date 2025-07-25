import asyncio
import logging
from typing import Optional

from aion.api import AionGqlApiClient

logger = logging.getLogger(__name__)


class AionWebSocketManager:
    """
    WebSocket connection manager for Aion platform.

    Provides basic agent communication with the Aion ecosystem through
    persistent WebSocket connection with automatic reconnection.
    """

    def __init__(self, ping_interval: float = 30.0, reconnect_delay: float = 10.0):
        """
        Initialize WebSocket manager.

        Args:
            ping_interval: Interval between ping messages (seconds)
            reconnect_delay: Delay before reconnection attempt (seconds)
        """
        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay

        self._aion_client: Optional[AionGqlApiClient] = None
        self._websocket_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._connection_ready = asyncio.Event()

    async def start(self) -> None:
        """Start WebSocket connection with the platform."""
        try:
            self._aion_client = AionGqlApiClient()
            logger.info("Initializing connection to Aion platform...")

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
                logger.info("Establishing WebSocket connection to Aion...")

                await self._establish_connection()

                if first_connection:
                    self._connection_ready.set()
                    first_connection = False
                    logger.info("First connection established, signaling ready")

                await self._run_ping_loop()

            except KeyboardInterrupt:
                logger.info("Connection stopped by user request")
                break
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")

                if first_connection:
                    logger.error("Failed to establish initial connection")
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

    async def _establish_connection(self) -> None:
        """Establish connection without blocking on pings."""
        transport = await self._aion_client._gql._build_transport()
        await transport.connect()
        logger.info("WebSocket connection established")

        self._transport = transport

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
            if hasattr(self, '_transport'):
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


aion_websocket_manager = AionWebSocketManager()
