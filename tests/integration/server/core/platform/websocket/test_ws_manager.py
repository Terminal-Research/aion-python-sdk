"""Exercise platform connection monitoring with real GraphQL WebSockets."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.server import serve

from aion.server.core.platform import AionWebSocketManager
from aion.server.core.platform.websocket import ws_manager as ws_manager_module
from aion.server.core.platform.websocket.transport import WebsocketTransportFactory


CLOSE_TIMEOUT = 0.1


class ShortCloseTimeoutFactory(WebsocketTransportFactory):
    """Exercise the real transport's teardown deadline without a ten-second test."""

    async def create_transport(self):
        transport = await super().create_transport()
        transport.close_timeout = CLOSE_TIMEOUT
        return transport


@pytest.fixture
async def live_connection(monkeypatch):
    monkeypatch.setattr(ws_manager_module, "LIVENESS_CHECK_INTERVAL", 0.02)
    connections = asyncio.Queue()

    async def accept(socket):
        assert socket.subprotocol == "graphql-transport-ws"
        assert json.loads(await socket.recv())["type"] == "connection_init"
        await socket.send(json.dumps({"type": "connection_ack"}))
        connections.put_nowait(socket)
        await socket.wait_closed()

    async with serve(
        accept, "127.0.0.1", 0, subprotocols=["graphql-transport-ws"]
    ) as server:
        port = server.sockets[0].getsockname()[1]
        factory = ShortCloseTimeoutFactory(
            f"ws://127.0.0.1:{port}/graphql",
            AsyncMock(get_token=AsyncMock(return_value=None)),
        )
        manager = AionWebSocketManager(
            factory, reconnect_delay=0.01, stop_timeout=0.5
        )
        try:
            yield manager, connections
        finally:
            await manager.stop()


async def test_healthy_connection_outlives_close_timeout(live_connection, caplog):
    """A teardown timeout must not force a healthy link to reconnect."""
    manager, connections = live_connection
    caplog.set_level(logging.DEBUG, logger=ws_manager_module.__name__)
    await manager.start()
    await asyncio.wait_for(connections.get(), timeout=2.0)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(connections.get(), timeout=CLOSE_TIMEOUT * 3)

    assert manager.is_connected
    assert manager.connection_state["reconnects"] == 0
    assert sum("connection alive" in r.getMessage() for r in caplog.records) >= 2
    assert not any("close_timeout fired" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("abrupt", [False, True], ids=["peer-close", "socket-drop"])
async def test_reconnects_after_real_socket_closure(live_connection, abrupt):
    """Both a closing handshake and a broken TCP connection trigger a redial."""
    manager, connections = live_connection
    await manager.start()
    socket = await asyncio.wait_for(connections.get(), timeout=2.0)

    if abrupt:
        socket.transport.abort()
    else:
        await socket.close(code=1012, reason="test server restart")

    replacement = await asyncio.wait_for(connections.get(), timeout=2.0)
    await asyncio.wait_for(manager._connected.wait(), timeout=2.0)
    assert replacement is not socket
    assert manager.is_connected
    assert manager.connection_state["reconnects"] == 1


async def test_stop_closes_real_socket_promptly(live_connection, caplog):
    """Waiting for actual socket closure must remain interruptible by shutdown."""
    manager, connections = live_connection
    await manager.start()
    socket = await asyncio.wait_for(connections.get(), timeout=2.0)
    loop_task = manager._websocket_task

    await asyncio.wait_for(manager.stop(), timeout=1.0)
    await asyncio.wait_for(socket.wait_closed(), timeout=1.0)

    assert loop_task.done()
    assert not loop_task.cancelled()
    assert not manager.is_connected
    assert connections.empty()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
