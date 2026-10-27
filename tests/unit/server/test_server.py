"""The agent process's HTTP server: what its shutdown does first, and how long it may take."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import uvicorn

from aion.server import server as server_module


async def test_the_extensions_turns_stop_once_nothing_new_is_accepted_and_before_open_connections_are_awaited(
    monkeypatch,
) -> None:
    order: list[str] = []
    listening = MagicMock()
    listening.close.side_effect = lambda: order.append("stop listening")
    connection = MagicMock()
    connection.shutdown.side_effect = lambda: order.append("close idle connections")

    async def before_drain() -> None:
        order.append("stop extension turns")

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
        "stop extension turns",
        "wait for open connections",
    ]


async def test_a_forced_exit_does_not_wait_for_the_extensions(monkeypatch) -> None:
    before_drain = AsyncMock()
    monkeypatch.setattr(uvicorn.Server, "shutdown", AsyncMock())
    server = server_module._AgentServer(uvicorn.Config(app=MagicMock()), before_drain=before_drain)
    server.servers = []
    server.force_exit = True

    await server.shutdown()

    before_drain.assert_not_awaited()


async def test_readiness_is_logged_after_successful_socket_startup(monkeypatch, caplog):
    async def startup(self, sockets=None):
        assert "Agent server ready" not in caplog.text
        self.started = True

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    server = server_module._AgentServer(uvicorn.Config(app=MagicMock()), before_drain=AsyncMock())
    with caplog.at_level("INFO", logger="aion.server.server"):
        await server.startup()
    assert "Agent server ready; accepting requests" in caplog.text


async def test_failed_lifespan_never_reports_readiness(monkeypatch, caplog):
    monkeypatch.setattr(uvicorn.Server, "startup", AsyncMock())
    server = server_module._AgentServer(uvicorn.Config(app=MagicMock()), before_drain=AsyncMock())
    with caplog.at_level("INFO", logger="aion.server.server"):
        await server.startup()
    assert "application did not become ready" in caplog.text
    assert "Agent server ready" not in caplog.text


async def test_process_start_and_stop_bracket_agent_initialization(monkeypatch, caplog):
    order = []
    monkeypatch.setattr(server_module, "setup_root_logger", lambda: order.append("logging"))

    async def serve(*args):
        assert order == ["logging"]
        assert "Agent server starting" in caplog.text
        assert "Agent server stopped" not in caplog.text

    monkeypatch.setattr(server_module, "async_serve", serve)
    with caplog.at_level("INFO", logger="aion.server.server"):
        await server_module.run_server("test-agent", MagicMock(), 1234)
    assert "Agent server stopped: agent_id=test-agent" in caplog.text
