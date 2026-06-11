"""Tests for the ``aion`` CLI entrypoint."""

from __future__ import annotations

import importlib
import asyncio
from threading import Event, Thread

from asyncclick.testing import CliRunner

from aion.cli.cli import __version__, cli
from aion.cli.services.chat import BinaryResolutionError


async def test_version() -> None:
    """Verify that the version flag renders the package version."""
    runner = CliRunner()

    result = await runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


async def test_help_lists_chat_command() -> None:
    """Ensure the help output advertises the experimental chat command."""
    runner = CliRunner()

    result = await runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "chat" in result.output
    assert "logs" in result.output


async def test_chat_launches_ui(monkeypatch) -> None:
    """Ensure ``aion chat`` forwards CLI flags into launch options."""
    runner = CliRunner()
    chat_module = importlib.import_module("aion.cli.commands.chat")
    called: dict[str, object] = {}

    def fake_launch(options):
        called["options"] = options
        return 0

    monkeypatch.setattr(chat_module, "launch_chat", fake_launch)

    result = await runner.invoke(
        cli,
        [
            "chat",
            "--url",
            "http://localhost:8000",
            "--agent-id",
            "demo-agent",
            "--token",
            "secret-token",
            "--header",
            "X-Test=one",
            "--push-notifications",
            "--push-receiver",
            "http://localhost:5050",
        ],
    )

    assert result.exit_code == 0
    options = called["options"]
    assert options.endpoint == "http://localhost:8000"
    assert options.agent_id == "demo-agent"
    assert options.token == "secret-token"
    assert options.headers == {"X-Test": "one"}
    assert options.push_notifications is True
    assert options.push_receiver == "http://localhost:5050"


async def test_chat_reports_missing_artifact(monkeypatch) -> None:
    """Ensure binary resolution failures surface as Click exceptions."""
    runner = CliRunner()
    chat_module = importlib.import_module("aion.cli.commands.chat")

    def fake_launch(_options):
        raise BinaryResolutionError("missing chat artifact")

    monkeypatch.setattr(chat_module, "launch_chat", fake_launch)

    result = await runner.invoke(cli, ["chat", "--url", "http://localhost:8000"])

    assert result.exit_code != 0
    assert "missing chat artifact" in result.output


async def test_chat_defaults_to_local_proxy(monkeypatch) -> None:
    """Ensure ``aion chat`` lets the UI resolve the selected environment."""
    runner = CliRunner()
    chat_module = importlib.import_module("aion.cli.commands.chat")
    called: dict[str, object] = {}

    def fake_launch(options):
        called["options"] = options
        return 0

    monkeypatch.setattr(chat_module, "launch_chat", fake_launch)

    result = await runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    options = called["options"]
    assert options.endpoint is None


async def test_chat_run_launches_headless_ui(monkeypatch) -> None:
    """Ensure ``aion chat run`` forwards one-shot request options."""
    runner = CliRunner()
    chat_module = importlib.import_module("aion.cli.commands.chat")
    called: dict[str, object] = {}

    def fake_launch(options):
        called["options"] = options
        return 0

    monkeypatch.setattr(chat_module, "launch_chat_run", fake_launch)

    result = await runner.invoke(
        cli,
        [
            "chat",
            "run",
            "--agent",
            "@team-agent",
            "--request-mode",
            "streaming-message",
            "--response-mode",
            "a2a",
            "hello",
            "there",
        ],
    )

    assert result.exit_code == 0
    options = called["options"]
    assert options.agent == "@team-agent"
    assert options.request_mode == "streaming-message"
    assert options.response_mode == "a2a"
    assert options.message == "hello there"


async def test_chat_run_help_describes_headless_usage() -> None:
    """Ensure ``aion chat run --help`` includes headless usage guidance."""
    runner = CliRunner()

    result = await runner.invoke(cli, ["chat", "run", "--help"])

    assert result.exit_code == 0
    assert "Agent selection:" in result.output
    assert "a2a mode writes raw A2A JSON" in result.output
    assert "aion chat run --agent @team-agent" in result.output


async def test_logs_tails_authenticated_version_logs(monkeypatch) -> None:
    """Ensure ``aion logs`` streams formatted version log events."""
    runner = CliRunner()
    logs_service = importlib.import_module("aion.cli.services.logs")
    called: dict[str, object] = {}

    class FakeClient:
        """Minimal client exposing the version log subscription."""

        async def version_logs(self, *, start_time: str):
            called["start_time"] = start_time
            yield {
                "versionLogs": {
                    "timestamp": "2026-05-14T15:00:01Z",
                    "level": "INFO",
                    "level_value": 20,
                    "message": "version ready",
                    "properties": [{"key": "requestId", "value": "req-1"}],
                }
            }

    class FakeContextClient:
        """Async context manager used instead of the real GraphQL client."""

        def __init__(self) -> None:
            called["initialized"] = True

        async def __aenter__(self):
            return FakeClient()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            called["closed"] = True

    monkeypatch.setattr(logs_service, "AionGqlContextClient", FakeContextClient)

    result = await runner.invoke(
        cli, ["logs", "--since", "2026-05-14T15:00:00Z"]
    )

    assert result.exit_code == 0
    assert called == {
        "initialized": True,
        "start_time": "2026-05-14T15:00:00Z",
        "closed": True,
    }
    assert "[2026-05-14T15:00:01Z] INFO version ready" in result.output
    assert "requestId=req-1" not in result.output


async def test_logs_can_include_properties(monkeypatch) -> None:
    """Ensure structured properties are opt-in for log output."""
    runner = CliRunner()
    logs_service = importlib.import_module("aion.cli.services.logs")

    class FakeClient:
        """Minimal client exposing the version log subscription."""

        async def version_logs(self, *, start_time: str):
            yield {
                "versionLogs": {
                    "timestamp": "2026-05-14T15:00:01Z",
                    "level": "ERROR",
                    "level_value": 40,
                    "message": "failed",
                    "properties": [{"key": "taskId", "value": "task-1"}],
                }
            }

    class FakeContextClient:
        """Async context manager used instead of the real GraphQL client."""

        async def __aenter__(self):
            return FakeClient()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    monkeypatch.setattr(logs_service, "AionGqlContextClient", FakeContextClient)

    result = await runner.invoke(
        cli,
        [
            "logs",
            "--since",
            "2026-05-14T15:00:00Z",
            "--properties",
        ],
    )

    assert result.exit_code == 0
    assert "[2026-05-14T15:00:01Z] ERROR failed taskId=task-1" in result.output


def test_logs_worker_cancellation_stops_subscription_task() -> None:
    """Ensure worker-thread cancellation stops the websocket task."""
    logs_command = importlib.import_module("aion.cli.commands.logs")
    started = Event()
    cancelled = Event()
    loop_ready = Event()
    state: dict[str, object] = {}
    errors: list[BaseException] = []

    async def blocking_subscription() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    thread = Thread(
        target=logs_command._run_coro_in_worker,
        args=(blocking_subscription(), loop_ready, state, errors),
        daemon=True,
    )
    thread.start()

    assert started.wait(timeout=1)
    logs_command._cancel_worker(loop_ready, state, thread, timeout=2)
    assert cancelled.wait(timeout=2)
    assert not thread.is_alive()
    assert errors == []


async def test_login_is_not_a_python_cli_command() -> None:
    """Ensure chat UI login remains scoped to the npm CLI and composer."""
    runner = CliRunner()

    result = await runner.invoke(cli, ["login"])

    assert result.exit_code != 0
    assert "No such command" in result.output


async def test_environment_is_not_a_python_cli_command() -> None:
    """Ensure chat UI environment switching remains scoped to npm and composer."""
    runner = CliRunner()

    result = await runner.invoke(cli, ["env", "development"])

    assert result.exit_code != 0
    assert "No such command" in result.output
