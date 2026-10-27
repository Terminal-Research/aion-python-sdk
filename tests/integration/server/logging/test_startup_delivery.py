"""Startup failures must reach the HTTP log sink before the process disappears.

These use real CLI/managed processes and the SDK's asynchronous Logstash
handler. All endpoints are loopback fixtures; no database or Aion account is
required. Fork and spawn run in isolated interpreters, not the pytest process.
"""

from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import os
import signal
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
DEPLOYMENT_ID = "00000000-0000-4000-8000-000000000002"
VERSION_ID = "00000000-0000-4000-8000-000000000003"


@pytest.fixture
def log_receiver():
    events = []
    public_key = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(
        ec.generate_private_key(ec.SECP256R1()).public_key()
    ))
    canonical = {key: public_key[key] for key in ("crv", "kty", "x", "y")}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).digest()
    public_key.update(kid=base64.urlsafe_b64encode(digest).decode().rstrip("="), alg="ES256", use="sig")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            # Hosted-mode startup validates the local JWKS even though no
            # request or real platform credential is involved in these tests.
            body = json.dumps({"keys": [public_key]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/":
                payload = json.loads(body)
                events.extend(payload if isinstance(payload, list) else [payload])
            self.send_response(200 if self.path == "/" else 503)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _environment(receiver_port):
    # Do not inherit a developer's credentials or endpoints, including .env.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT") if key in os.environ}
    env.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONUNBUFFERED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "NO_PROXY": "127.0.0.1,localhost",
        "AION_API_HOST": f"http://127.0.0.1:{receiver_port}",
        "AION_CLIENT_ID": "00000000-0000-4000-8000-000000000004",
        "ORGANIZATION_ID": ORGANIZATION_ID,
        "DEPLOYMENT_ID": DEPLOYMENT_ID,
        "VERSION_ID": VERSION_ID,
        "LOGSTASH_HOST": "127.0.0.1",
        "LOGSTASH_PORT": str(receiver_port),
    })
    return env


def _run(tmp_path, script, env, *args):
    launcher = tmp_path / "launch.py"
    launcher.write_text(script, encoding="utf-8")
    with subprocess.Popen(
        [sys.executable, str(launcher), *args], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    ) as process:
        try:
            output, _ = process.communicate(timeout=45)
        finally:
            if process.poll() is None:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
    return process.returncode, output


def _assert_attribution(event):
    tags = event["_tags"]
    assert tags["aion.organization.id"] == ORGANIZATION_ID
    assert tags["aion.deployment.id"] == DEPLOYMENT_ID
    assert tags["aion.version.id"] == VERSION_ID


@pytest.mark.parametrize("failure", ["malformed", "unreachable-fork", "unreachable-spawn"])
def test_cli_delivers_database_startup_failure(tmp_path, log_receiver, failure):
    method = failure.removeprefix("unreachable-") if failure != "malformed" else "spawn"
    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{method} is unavailable")
    port, events = log_receiver
    env = _environment(port)
    (tmp_path / "aion.yaml").write_text(
        'aion:\n  agents:\n    probe:\n      path: "probe_agent:create_agent"\n', encoding="utf-8"
    )
    (tmp_path / "probe_agent.py").write_text(
        'def create_agent():\n    raise AssertionError("Database failure should precede agent loading")\n'
    )
    # Reserve but do not listen on the DB port, making connection failure local
    # and deterministic without reaching any installed PostgreSQL instance.
    with socket.socket() as unavailable, socket.socket() as server_port:
        unavailable.bind(("127.0.0.1", 0))
        server_port.bind(("127.0.0.1", 0))
        proxy_port = server_port.getsockname()[1]
        server_port.close()
        scheme = "postgres" if failure == "malformed" else "postgresql"
        env["POSTGRES_URL"] = f"{scheme}://probe:PRIVATE_PASSWORD@127.0.0.1:{unavailable.getsockname()[1]}/probe?connect_timeout=2"
        code, output = _run(tmp_path, f"""
import multiprocessing
if __name__ == '__main__':
    multiprocessing.set_start_method({method!r})
    from aion.cli.cli import cli
    cli()
""", env, "serve", "--port", str(proxy_port), "--port-range-start", "40000", "--port-range-end", "41000", "--startup-timeout", "10")

    assert code != 0, output
    assert "PRIVATE_PASSWORD" not in output + json.dumps(events)
    errors = [event for event in events if event["logLevel"] == "ERROR"]
    assert errors, output
    for event in errors:
        _assert_attribution(event)
    if failure == "malformed":
        assert any("POSTGRES_URL" in event["message"] and "expected postgresql://" in event["message"] for event in errors), output
    else:
        assert any(event["logger"] == "aion.db.postgres.utils" and "Could not connect" in event["message"] for event in errors), output
        assert any(event.get("error.type") == "RuntimeError" and "refusing to fall back" in event.get("error.message", "") for event in errors), output


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="fork unavailable")
@pytest.mark.parametrize(("exit_kind", "expected_code"), [("return", 0), ("raise", 1), ("exit", 7)])
def test_managed_child_delivers_last_log_before_exit(tmp_path, log_receiver, exit_kind, expected_code):
    port, events = log_receiver
    code, output = _run(tmp_path, f"""
import logging
import multiprocessing
from aion.server.logging import setup_root_logger
from aion.server.utils.processes.process_manager import ProcessManager

def target():
    setup_root_logger()
    logging.getLogger('managed.probe').error('last log before {exit_kind}')
    if {exit_kind!r} == 'raise':
        raise RuntimeError('managed startup failed')
    if {exit_kind!r} == 'exit':
        raise SystemExit(7)

if __name__ == '__main__':
    multiprocessing.set_start_method('fork')
    manager = ProcessManager()
    assert manager.create_process('probe', target)
    child = manager.processes['probe'].process
    child.join(timeout=15)
    if child.is_alive():
        manager.terminate_process('probe', timeout=2)
        raise AssertionError('child did not finish its logging shutdown')
    raise SystemExit(child.exitcode)
""", _environment(port))
    assert code == expected_code, output
    matching = [event for event in events if event["message"] == f"last log before {exit_kind}"]
    assert len(matching) == 1, (output, events)
    _assert_attribution(matching[0])
