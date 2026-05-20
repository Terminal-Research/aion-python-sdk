"""Tests for the standalone chat launcher."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aion.cli.services.chat import launcher


def test_chat_launch_options_to_args() -> None:
    """Ensure launcher arguments are serialized for the UI binary."""
    options = launcher.ChatLaunchOptions(
        endpoint="http://localhost:8000",
        agent_id="demo-agent",
        token="secret-token",
        headers={"X-Test": "one"},
        push_notifications=True,
        push_receiver="http://localhost:5050",
    )

    assert options.to_args() == [
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
    ]


def test_chat_launch_options_omits_endpoint_when_not_supplied() -> None:
    """Ensure environment-backed chat launches do not force a local URL."""
    options = launcher.ChatLaunchOptions(
        endpoint=None,
        agent_id=None,
        token=None,
        headers={},
        push_notifications=False,
        push_receiver="http://localhost:5000",
    )

    assert options.to_args() == []


def test_chat_run_launch_options_to_args() -> None:
    """Ensure headless run arguments are serialized for the UI binary."""
    options = launcher.ChatRunLaunchOptions(
        endpoint="http://localhost:8000",
        agent_id="demo-agent",
        agent="@team-agent",
        token="secret-token",
        headers={"X-Test": "one"},
        push_notifications=True,
        push_receiver="http://localhost:5050",
        request_mode="streaming-message",
        response_mode="a2a",
        message="hello there",
    )

    assert options.to_args() == [
        "run",
        "--url",
        "http://localhost:8000",
        "--agent-id",
        "demo-agent",
        "--agent",
        "@team-agent",
        "--token",
        "secret-token",
        "--header",
        "X-Test=one",
        "--push-notifications",
        "--push-receiver",
        "http://localhost:5050",
        "--request-mode",
        "streaming-message",
        "--response-mode",
        "a2a",
        "hello there",
    ]


def test_resolve_chat_command_prefers_packaged_binary(
    monkeypatch, tmp_path: Path
) -> None:
    """Ensure packaged binaries win when no source-checkout UI is available."""
    binary = tmp_path / "aion-chat-ui-darwin-arm64"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setattr(launcher, "_packaged_resource_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_platform_binary_name", lambda: binary.name)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(launcher, "_repo_root_from_checkout", lambda: None)

    assert launcher.resolve_chat_command() == [str(binary)]


def test_resolve_chat_command_falls_back_to_packaged_bundle(
    monkeypatch, tmp_path: Path
) -> None:
    """Ensure unsupported binary platforms still fall back to the bundled JS UI."""
    bundle = tmp_path / "cli.mjs"
    bundle.write_text("console.log('chat');\n")

    monkeypatch.setattr(launcher, "_packaged_resource_root", lambda: tmp_path)
    monkeypatch.setattr(
        launcher,
        "_platform_binary_name",
        lambda: (_ for _ in ()).throw(launcher.BinaryResolutionError("unsupported")),
    )
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(launcher, "_repo_root_from_checkout", lambda: None)

    assert launcher.resolve_chat_command() == ["/usr/bin/node", str(bundle)]


def test_resolve_chat_command_prefers_checkout_bundle_over_packaged_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    """Ensure source-checkout bundles win during local editable development."""
    repo_root = tmp_path / "repo"
    checkout_bundle = repo_root / "libs" / "aion-chat-ui" / "dist" / "cli.mjs"
    checkout_bundle.parent.mkdir(parents=True)
    checkout_bundle.write_text("console.log('checkout');\n")
    packaged_bundle = tmp_path / "cli.mjs"
    packaged_bundle.write_text("console.log('packaged');\n")
    binary = tmp_path / "aion-chat-ui-darwin-arm64"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setattr(launcher, "_packaged_resource_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_platform_binary_name", lambda: binary.name)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(launcher, "_repo_root_from_checkout", lambda: repo_root)

    assert launcher.resolve_chat_command() == [
        "/usr/bin/node",
        str(checkout_bundle),
    ]


def test_launch_chat_returns_process_exit_code(monkeypatch) -> None:
    """Ensure the launcher returns the child process exit status."""
    options = launcher.ChatLaunchOptions(
        endpoint=None,
        agent_id=None,
        token=None,
        headers={},
        push_notifications=False,
        push_receiver="http://localhost:5000",
    )
    recorded: dict[str, object] = {}

    def fake_runner(command, check, env):
        recorded["command"] = command
        recorded["check"] = check
        recorded["env"] = env
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(launcher, "resolve_chat_command", lambda: ["node", "/tmp/cli.mjs"])

    exit_code = launcher.launch_chat(options, runner=fake_runner)

    assert exit_code == 7
    assert recorded["command"] == [
        "node",
        "/tmp/cli.mjs",
    ]
    assert recorded["check"] is False
    assert isinstance(recorded["env"], dict)
    assert recorded["env"]["AION_CHAT_SKIP_UPDATE_CHECK"] == "1"


def test_launch_chat_run_returns_process_exit_code(monkeypatch) -> None:
    """Ensure headless launches include the run subcommand and return child status."""
    options = launcher.ChatRunLaunchOptions(
        endpoint=None,
        agent_id=None,
        agent="@team-agent",
        token=None,
        headers={},
        push_notifications=False,
        push_receiver="http://localhost:5000",
        request_mode="send-message",
        response_mode="message",
        message="hello",
    )
    recorded: dict[str, object] = {}

    def fake_runner(command, check, env):
        recorded["command"] = command
        recorded["check"] = check
        recorded["env"] = env
        return SimpleNamespace(returncode=5)

    monkeypatch.setattr(launcher, "resolve_chat_command", lambda: ["node", "/tmp/cli.mjs"])

    exit_code = launcher.launch_chat_run(options, runner=fake_runner)

    assert exit_code == 5
    assert recorded["command"] == [
        "node",
        "/tmp/cli.mjs",
        "run",
        "--agent",
        "@team-agent",
        "--request-mode",
        "send-message",
        "--response-mode",
        "message",
        "hello",
    ]
    assert recorded["check"] is False
    assert isinstance(recorded["env"], dict)
    assert recorded["env"]["AION_CHAT_SKIP_UPDATE_CHECK"] == "1"
