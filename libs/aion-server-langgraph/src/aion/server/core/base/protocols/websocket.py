from typing import Protocol


class WebSocketManagerProto(Protocol):
    """Protocol for WebSocket manager implementations."""

    async def start(self) -> None:
        """
        Start WebSocket connection with the platform.

        Raises:
            ConnectionError: If failed to establish connection.
            asyncio.TimeoutError: If connection timeout occurs.
        """
        ...

    async def stop(self) -> None:
        """
        Stop WebSocket connection.

        Gracefully closes the connection and cleans up resources.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """
        Check if connection to the platform is active.

        Returns:
            True if connection is active, False otherwise.
        """
        ...
