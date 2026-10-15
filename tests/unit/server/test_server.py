"""The agent process's HTTP server: what its shutdown does first, and how long it may take."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import uvicorn

from aion.server import server as server_module


async def test_the_toolkits_turns_stop_once_nothing_new_is_accepted_and_before_open_connections_are_awaited(
    monkeypatch,
) -> None:
    order: list[str] = []
    listening = MagicMock()
    listening.close.side_effect = lambda: order.append("stop listening")
    connection = MagicMock()
    connection.shutdown.side_effect = lambda: order.append("close idle connections")

    async def before_drain() -> None:
        order.append("stop toolkit turns")

    async def uvicorn_shutdown(self, sockets=None) -> None:
        order.append("wait for open connections")

    monkeypatch.setattr(uvicorn.Server, "shutdown", uvicorn_shutdown)
    server = server_module._AgentServer(uvicorn.Config(app=MagicMock()), before_drain=before_drain)
    server.servers = [listening]
    server.server_state.connections.add(connection)

    await server.shutdown()

    assert order == [
        "stop listening",
        "close idle connections",
        "stop toolkit turns",
        "wait for open connections",
    ]


async def test_a_forced_exit_does_not_wait_for_the_toolkits(monkeypatch) -> None:
    before_drain = AsyncMock()
    monkeypatch.setattr(uvicorn.Server, "shutdown", AsyncMock())
    server = server_module._AgentServer(uvicorn.Config(app=MagicMock()), before_drain=before_drain)
    server.servers = []
    server.force_exit = True

    await server.shutdown()

    before_drain.assert_not_awaited()
