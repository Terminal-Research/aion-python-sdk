import asyncio

import pytest


class FakeTransport:
    """Stand-in for gql's WebsocketsTransport with a close we can trigger.

    The manager learns that a connection died by awaiting wait_closed(), so tests
    need a transport whose close they can fire on demand rather than a bare mock
    whose wait_closed() would return immediately and spin the loop.
    """

    # gql keeps the auth token in the URL query, exactly as the real factory builds it.
    url = "wss://api-staging.aion.to/ws/graphql?token=super-secret-jwt"

    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.websocket = object()
        self.close_exception = None
        self.connect_calls = 0
        self.close_calls = 0
        self._closed = asyncio.Event()

    async def connect(self):
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def wait_closed(self):
        await self._closed.wait()

    async def close(self):
        self.close_calls += 1
        self.drop(None)

    def drop(self, error=None):
        """Simulate the transport closing underneath the manager."""
        self.websocket = None
        self.close_exception = error
        self._closed.set()


class FakeTransportFactory:
    """Hands out a fresh transport per dial, as the real factory does."""

    def __init__(self, transports=None):
        self.transports = list(transports or [])
        self.created = []

    async def create_transport(self):
        transport = self.transports.pop(0) if self.transports else FakeTransport()
        self.created.append(transport)
        return transport


@pytest.fixture
def make_transport():
    """Expose the fake so tests can queue up ones whose connect fails."""
    return FakeTransport


@pytest.fixture
def transport_factory():
    return FakeTransportFactory()


@pytest.fixture
def ws_manager(transport_factory):
    """Create AionWebSocketManager instance with mocked dependencies."""
    from aion.server.core.platform.websocket import AionWebSocketManager

    return AionWebSocketManager(
        ws_transport_factory=transport_factory,
        reconnect_delay=0.01,
        max_reconnect_delay=0.05,
        connect_timeout=0.5,
        startup_timeout=1.0,
        stop_timeout=0.5,
    )


@pytest.fixture
async def started_ws_manager(ws_manager):
    """Pre-started WebSocket manager for tests that need active connection."""
    await ws_manager.start()
    yield ws_manager
    await ws_manager.stop()
