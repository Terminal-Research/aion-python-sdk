"""Tests for the ``aion`` CLI entrypoint."""

from __future__ import annotations

import importlib

from click.testing import CliRunner

from aion.cli.cli import __version__, cli
from aion.cli.services.chat import BinaryResolutionError


def test_version() -> None:
    """Verify that the version flag renders the package version."""
    runner = CliRunner()

    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_chat_command() -> None:
    """Ensure the help output advertises the experimental chat command."""
    runner = CliRunner()

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "chat" in result.output


def test_chat_launches_ui(monkeypatch) -> None:
    """Ensure ``aion chat`` forwards CLI flags into launch options."""
    runner = CliRunner()
    chat_module = importlib.import_module("aion.cli.commands.chat")
    called: dict[str, object] = {}

    def fake_launch(options):
        called["options"] = options
        return 0

    monkeypatch.setattr(chat_module, "launch_chat", fake_launch)

    result = runner.invoke(
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


def test_chat_reports_missing_artifact(monkeypatch) -> None:
    """Ensure binary resolution failures surface as Click exceptions."""
    runner = CliRunner()
    chat_module = importlib.import_module("aion.cli.commands.chat")

    def fake_launch(_options):
        raise BinaryResolutionError("missing chat artifact")

    monkeypatch.setattr(chat_module, "launch_chat", fake_launch)

    result = runner.invoke(cli, ["chat", "--url", "http://localhost:8000"])

    assert result.exit_code != 0
    assert "missing chat artifact" in result.output


def test_chat_defaults_to_local_proxy(monkeypatch) -> None:
    """Ensure ``aion chat`` lets the UI resolve the selected environment."""
    runner = CliRunner()
    chat_module = importlib.import_module("aion.cli.commands.chat")
    called: dict[str, object] = {}

    def fake_launch(options):
        called["options"] = options
        return 0

    monkeypatch.setattr(chat_module, "launch_chat", fake_launch)

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    options = called["options"]
    assert options.endpoint is None


def test_login_is_not_a_python_cli_command() -> None:
    """Ensure chat UI login remains scoped to the npm CLI and composer."""
    runner = CliRunner()

    result = runner.invoke(cli, ["login"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_environment_is_not_a_python_cli_command() -> None:
    """Ensure chat UI environment switching remains scoped to npm and composer."""
    runner = CliRunner()

    result = runner.invoke(cli, ["env", "development"])

    assert result.exit_code != 0
    assert "No such command" in result.output
