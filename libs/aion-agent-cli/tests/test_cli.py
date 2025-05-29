from click.testing import CliRunner
from aion.cli.cli import cli, __version__
import logging
import types
import sys


def test_version() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_serve_invokes_server(monkeypatch) -> None:
    """Ensure the serve command delegates to the example server."""
    called = {}

    def fake_server(host: str, port: int) -> None:
        called["args"] = (host, port)

    mod = types.SimpleNamespace(main=types.SimpleNamespace(callback=fake_server))
    monkeypatch.setitem(sys.modules, "aion.server.langgraph.__main__", mod)

    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--host", "0.0.0.0", "--port", "1234"])

    assert result.exit_code == 0
    assert called["args"] == ("0.0.0.0", 1234)
