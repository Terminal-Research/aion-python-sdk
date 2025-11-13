from abc import ABC, abstractmethod

from gql.transport.websockets import WebsocketsTransport

__all__ = [
    "IWebSocketManager",
    "IWebsocketTransportFactory",
]


class IWebSocketManager(ABC):
    """Protocol for WebSocket manager implementations."""

    @abstractmethod
    async def start(self) -> None:
        """
        Start WebSocket connection with the platform.

        Raises:
            ConnectionError: If failed to establish connection.
            asyncio.TimeoutError: If connection timeout occurs.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """
        Stop WebSocket connection.

        Gracefully closes the connection and cleans up resources.
        """
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """
        Check if connection to the platform is active.

        Returns:
            True if connection is active, False otherwise.
        """
        ...


class IWebsocketTransportFactory(ABC):
    """Protocol for WebSocket manager implementations."""

    @abstractmethod
    async def create_transport(self) -> WebsocketsTransport:
        ...
