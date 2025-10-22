from typing import Optional, List

from gql.transport.websockets import WebsocketsTransport

from aion.server.interfaces import IWebsocketTransportFactory, IAuthManager

__all__ = [
    "WebsocketTransportFactory",
]


class WebsocketTransportFactory(IWebsocketTransportFactory):
    """Factory for creating authenticated WebSocket transports for GraphQL."""

    def __init__(
            self,
            ws_url: str,
            auth_manager: IAuthManager,
            keepalive: int = 60,
            subprotocols: Optional[List[str]] = None,
    ):
        """Initialize the WebSocket transport factory.

        Args:
            ws_url: Base WebSocket URL for connection.
            auth_manager: Authentication manager for token retrieval.
            keepalive: Ping interval in seconds for connection keepalive.
            subprotocols: List of WebSocket subprotocols to support.
        """
        self.ws_url = ws_url
        self.auth_manager = auth_manager
        self.keepalive = keepalive
        self.subprotocols = subprotocols or ["graphql-transport-ws"]

    async def create_transport(self) -> WebsocketsTransport:
        """Create an authenticated WebSocket transport.

        Returns:
            Configured WebsocketsTransport instance with authentication.
        """
        auth_token = await self.auth_manager.get_token()
        if auth_token:
            url = f"{self.ws_url}?token={auth_token}"
        else:
            url = self.ws_url

        return WebsocketsTransport(
            url=url,
            ping_interval=self.keepalive,
            subprotocols=["graphql-transport-ws"])
