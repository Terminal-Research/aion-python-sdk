"""Explicit development API selection through the real Python CLI parser."""
import importlib

import pytest
from asyncclick.testing import CliRunner

from aion.cli.cli import cli

ORIGIN = "https://dev.example.test"


@pytest.mark.parametrize("args", [
    ["--env", "development", ORIGIN],
    ["--env=development", ORIGIN],
    ["run", "--env", "development", ORIGIN, "hello"],
    ["--env", "development", ORIGIN, "run", "hello"],
])
async def test_chat_origin_forwarding(monkeypatch, args):
    module = importlib.import_module("aion.cli.commands.chat")
    calls = []
    monkeypatch.setattr(module, "launch_chat", lambda options: calls.append(options) or 0)
    monkeypatch.setattr(module, "launch_chat_run", lambda options: calls.append(options) or 0)
    result = await CliRunner().invoke(cli, ["chat", *args])
    assert result.exit_code == 0, result.output
    assert calls[0].environment_id == "development"
    assert calls[0].control_plane_origin == ORIGIN
    assert calls[0].to_args()[:3] == (["run", "--env", "development"] if "run" in args else ["--env", "development", ORIGIN])


@pytest.mark.parametrize("args, message", [
    (["--env", "development", "hello"], "hello"),
    (["--env", "development", "--", ORIGIN], ORIGIN),
    (["--token", "--env", "hello"], "hello"),
])
async def test_optional_origin_does_not_consume_message(monkeypatch, args, message):
    module = importlib.import_module("aion.cli.commands.chat")
    calls = []
    monkeypatch.setattr(module, "launch_chat_run", lambda options: calls.append(options) or 0)
    result = await CliRunner().invoke(cli, ["chat", "run", *args])
    assert result.exit_code == 0, result.output
    assert calls[0].control_plane_origin is None
    assert calls[0].message == message
    assert calls[0].to_args()[-2:] == ["--", message]


@pytest.mark.parametrize("environment, origin", [
    ("production", ORIGIN), ("staging", ORIGIN),
    ("development", "https://user:secret@dev.test"),
    ("development", "https://dev.test/api"),
    ("development", "ftp://dev.test"),
    ("development", "https://dev.test?key=secret"),
])
async def test_invalid_origins_fail_before_launch(monkeypatch, environment, origin):
    module = importlib.import_module("aion.cli.commands.chat")
    calls = []
    monkeypatch.setattr(module, "launch_chat", lambda options: calls.append(options) or 0)
    result = await CliRunner().invoke(cli, ["chat", "--env", environment, origin])
    assert result.exit_code == 2
    assert "Custom API origins" in result.output
    assert calls == []


@pytest.mark.parametrize("args", [
    ["--workspace", "Terminal Research"],
    ["run", "--workspace", "Terminal Research", "--", "--env hello"],
    ["--workspace", "Terminal Research", "run", "hello"],
    ["run", "--env", "development", ORIGIN, "--workspace", "Terminal Research", "hello"],
])
async def test_workspace_forwarding(monkeypatch, args):
    module = importlib.import_module("aion.cli.commands.chat")
    calls = []
    monkeypatch.setattr(module, "launch_chat", lambda options: calls.append(options) or 0)
    monkeypatch.setattr(module, "launch_chat_run", lambda options: calls.append(options) or 0)
    result = await CliRunner().invoke(cli, ["chat", *args])
    assert result.exit_code == 0, result.output
    assert calls[0].workspace == "Terminal Research"
    argv = calls[0].to_args()
    assert argv[argv.index("--workspace") + 1] == "Terminal Research"
    if "--" in args:
        assert argv[-2:] == ["--", "--env hello"]


@pytest.mark.parametrize("prefix", [[], ["run"]])
async def test_workspace_rejects_direct_url(monkeypatch, prefix):
    module = importlib.import_module("aion.cli.commands.chat")
    calls = []
    monkeypatch.setattr(module, "launch_chat", lambda options: calls.append(options) or 0)
    monkeypatch.setattr(module, "launch_chat_run", lambda options: calls.append(options) or 0)
    result = await CliRunner().invoke(cli, ["chat", *prefix, "--workspace", "Team", "--url", "http://localhost:8000"])
    assert result.exit_code == 2
    assert "cannot be combined" in result.output
    assert not calls
