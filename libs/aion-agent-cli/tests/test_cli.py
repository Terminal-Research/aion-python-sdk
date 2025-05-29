from click.testing import CliRunner
from aion.cli.cli import cli, __version__
import logging


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


def test_serve_outputs_message(caplog) -> None:
    runner = CliRunner()
    with caplog.at_level(logging.INFO):
        result = runner.invoke(cli, ["serve"])
    assert result.exit_code == 0
    assert "Welcome to" in caplog.text
