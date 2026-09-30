"""Exercise the real Node bundle, Python launcher and one-shot keychain helper."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aion.cli.services.chat import launcher


def test_packaged_guest_delivery_across_launcher_restarts(monkeypatch, tmp_path):
    """A restart reuses the guest through the real helper without touching login."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("The chat CLI requires Node 22 or newer")
    root = Path(__file__).resolve().parents[3]
    fixtures = Path(__file__).parent / "fixtures"
    keychain = tmp_path / "keychain.json"
    requests = tmp_path / "requests.jsonl"
    config = tmp_path / "aion"
    config.mkdir()
    (config / "chat2.json").write_text(json.dumps({"selectedEnvironment": "development"}))
    # Keep an unrelated account credential to prove guest writes do not replace it.
    keychain.write_text(json.dumps({"aion-chat-python": {"production:user": "fixture-account"}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AION_CHAT_TEST_KEYCHAIN", str(keychain))
    monkeypatch.setenv("AION_CHAT_TEST_REQUESTS", str(requests))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "chat_test_keyring.Keyring")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(fixtures), str(root / "src")]))
    monkeypatch.setenv("NODE_OPTIONS", f"--import={fixtures / 'chat_receiver.mjs'}")
    monkeypatch.setenv("AION_CHAT_SKIP_UPDATE_CHECK", "1")
    commands = [
        [node, str(root / "src/aion/cli/bin/cli.mjs"), "run", "--agent", "demo", "hello"],
        [sys.executable, "-m", "aion.cli", "chat", "run", "--agent", "demo", "hello"],
    ]
    # The npm entrypoint uses the same helper seam here; native Node keyring
    # selection is covered by the client store tests. Never access the real keychain.
    npm_bundle = root / "libs/aion-chat-ui/dist/cli.mjs"
    if npm_bundle.exists():
        commands.append(
            [node, str(root / "libs/aion-chat-ui/bin/aio"), "run", "--agent", "demo", "hello"]
        )
    for command in commands:
        result = subprocess.run(
            command,
            env=launcher.build_chat_environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Packaged guest reply" in result.stdout
        assert "fixture-guest-bearer" not in result.stdout + result.stderr
    traffic = [json.loads(line) for line in requests.read_text().splitlines()]
    assert sum(row["url"].endswith("/auth/anonymous-sessions") for row in traffic) == 1
    assert all(
        row["authorization"] == "Bearer fixture-guest-bearer"
        for row in traffic if row["url"].startswith("http://localhost:8000")
    )
    values = json.loads(keychain.read_text())["aion-chat-python"]
    assert values["production:user"] == "fixture-account"
    guest_keys = [key for key in values if key.startswith("aion-chat:anonymous-session:v1:")]
    assert len(guest_keys) == 1
    assert json.loads(values[guest_keys[0]])["sessionId"].endswith("000000000001")
    for log in (config / "chat-session-logs").glob("*.jsonl"):
        assert "fixture-guest-bearer" not in log.read_text()
