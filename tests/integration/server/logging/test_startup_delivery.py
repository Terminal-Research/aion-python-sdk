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
            body = json.dumps(
                events if self.path == "/startup-events" else {"keys": [public_key]}
            ).encode()
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
        db_error = next(event for event in errors if event["logger"] == "aion.db.postgres.utils")
        assert db_error["span.name"] == "database.prepare"
        child = next(event for event in events if event["message"] == "agent.startup failed")
        parent = next(event for event in events if event["message"] == "application.startup failed")
        assert db_error["trace.id"] == child["trace.id"] == parent["trace.id"]
        assert db_error["parent.span.id"] == child["span.id"]
        assert child["parent.span.id"] == parent["span.id"]
    assert all(event.get("trace.id") for event in errors), (output, errors)


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_cli_startup_connects_agents_and_proxy_but_not_runtime_requests(tmp_path, log_receiver, method):
    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{method} is unavailable")
    port, events = log_receiver
    env = _environment(port)
    (tmp_path / "aion.yaml").write_text(
        'aion:\n  agents:\n    first:\n      path: "probe_agent:create_agent"\n'
        '    second:\n      path: "probe_agent:create_agent"\n', encoding="utf-8"
    )
    (tmp_path / "probe_agent.py").write_text('''
import logging
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    text: str

def create_agent():
    logging.getLogger("startup.probe").info("probe agent built")
    graph = StateGraph(State)
    graph.add_node("echo", lambda state: state)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    return graph
''', encoding="utf-8")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        proxy_port = reserved.getsockname()[1]
    code, output = _run(tmp_path, f'''
import multiprocessing

if __name__ == '__main__':
    multiprocessing.set_start_method({method!r})
    import logging
    import asyncio
    import httpx
    from aion.cli.handlers.serve import ServeHandler
    from aion.cli.cli import cli

    async def monitor(self):
        # Receiving the readiness message must mean sockets are listening.
        async with httpx.AsyncClient() as client:
            for agent in ("first", "second"):
                response = await client.get(
                    f"http://127.0.0.1:{proxy_port}/agents/{{agent}}/.well-known/agent-card.json")
                assert response.status_code == 200, response.text
        logging.getLogger("startup.probe").info("runtime supervisor")
        # Logstash batches asynchronously. Observe delivery while these real
        # servers still run, before their SIGTERM shutdown can end the process.
        async with asyncio.timeout(25), httpx.AsyncClient() as client:
            while True:
                delivered = (await client.get("http://127.0.0.1:{port}/startup-events")).json()
                ready = [event for event in delivered if event["message"] in
                         ("agent.startup ready", "proxy.startup ready", "application.startup ready")]
                requests = [event for event in delivered if event["logger"] ==
                            "aion.server.core.middlewares.tracing"]
                if len(ready) == 4 and len(requests) == 2:
                    break
                await asyncio.sleep(0.05)

    ServeHandler._monitor = monitor
    cli()
''', env, "serve", "--port", str(proxy_port), "--port-range-start", "40000",
        "--port-range-end", "41000", "--startup-timeout", "20")
    assert code == 0, output
    parent, = [event for event in events if event["message"] == "application.startup ready"]
    children = [event for event in events if event["message"] in ("agent.startup ready", "proxy.startup ready")]
    assert len(children) == 3, output
    assert len({event["process.pid"] for event in children}) == 3
    assert len({event["span.id"] for event in children}) == 3
    for child in children:
        _assert_attribution(child)
        assert child["trace.id"] == parent["trace.id"]
        assert child["parent.span.id"] == parent["span.id"]
    built = [event for event in events if event["message"] == "probe agent built"]
    assert len(built) == 2
    assert all(event["trace.id"] == parent["trace.id"] and event["span.name"] == "agent.build" for event in built)
    requests = [event for event in events if event["logger"] == "aion.server.core.middlewares.tracing"]
    assert len(requests) == 2, output
    assert all(event["trace.id"] and event["trace.id"] != parent["trace.id"] for event in requests)
    background, = [event for event in events if event["message"] == "runtime supervisor"]
    assert not background.get("trace.id")


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_restarting_a_managed_process_does_not_replay_the_initial_trace(tmp_path, log_receiver, method):
    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{method} is unavailable")
    port, events = log_receiver
    code, output = _run(tmp_path, f'''
import logging
import multiprocessing
from aion.server.logging import setup_root_logger
from aion.server.opentelemetry.startup import StartupSpan
from aion.server.utils.processes.process_manager import ProcessManager

def target(conn=None, startup_context=None):
    setup_root_logger()
    startup = StartupSpan("probe.startup", startup_context)
    with startup.activate():
        logging.getLogger("startup.probe").info("managed child startup")
    startup.finish()
    logging.getLogger("startup.probe").info("managed child runtime")

if __name__ == '__main__':
    multiprocessing.set_start_method({method!r})
    setup_root_logger()
    manager = ProcessManager()
    startup = StartupSpan("application.startup")
    try:
        with startup.activate():
            assert manager.create_process("probe", target, use_pipe=True, propagate_trace=True)
            manager.processes["probe"].process.join(timeout=15)
            assert manager.processes["probe"].process.exitcode == 0
        startup.finish()
        assert manager.restart_process("probe")
        manager.processes["probe"].process.join(timeout=15)
        assert manager.processes["probe"].process.exitcode == 0
    finally:
        manager.shutdown_all(timeout=2)
''', _environment(port))
    assert code == 0, output
    children = [event for event in events if event["message"] == "managed child startup"]
    assert len(children) == 2, output
    assert len({event["trace.id"] for event in children}) == 2
    assert children[0].get("parent.span.id")
    assert not children[1].get("parent.span.id")
    runtime = [event for event in events if event["message"] == "managed child runtime"]
    assert len(runtime) == 2
    assert all(not event.get("trace.id") for event in runtime)


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_cli_restarts_a_dead_proxy_with_a_fresh_startup_trace(tmp_path, log_receiver, method):
    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{method} is unavailable")
    port, events = log_receiver
    env = _environment(port)
    (tmp_path / "aion.yaml").write_text(
        'aion:\n  agents:\n    probe:\n      path: "probe_agent:create_agent"\n', encoding="utf-8"
    )
    (tmp_path / "probe_agent.py").write_text('''
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    text: str

def create_agent():
    graph = StateGraph(State)
    graph.add_node("echo", lambda state: state)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    return graph
''', encoding="utf-8")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        proxy_port = reserved.getsockname()[1]
    code, output = _run(tmp_path, f'''
import multiprocessing

if __name__ == '__main__':
    multiprocessing.set_start_method({method!r})
    import asyncio
    import os
    import signal
    import httpx
    from aion.cli.handlers.serve import ServeHandler
    from aion.cli.services.serve.monitoring import ServeMonitoringService
    from aion.cli.cli import cli

    ServeMonitoringService.check_interval = 0.2
    supervise = ServeHandler._monitor

    async def delivered(client, message, count):
        while True:
            events = (await client.get("http://127.0.0.1:{port}/startup-events")).json()
            if sum(event["message"] == message for event in events) == count:
                return
            await asyncio.sleep(0.05)

    async def monitor(self):
        async with asyncio.timeout(30), httpx.AsyncClient() as client:
            # SIGKILL drops what the proxy has not shipped yet.
            await delivered(client, "proxy.startup ready", 1)
        proxy = self.process_manager.get_process_info("proxy").process
        os.kill(proxy.pid, signal.SIGKILL)
        await asyncio.to_thread(proxy.join, 10)
        monitoring = asyncio.ensure_future(supervise(self))
        try:
            async with asyncio.timeout(30), httpx.AsyncClient() as client:
                # The restarted proxy serves on the port the launcher kept reserved.
                while True:
                    try:
                        response = await client.get(
                            f"http://127.0.0.1:{proxy_port}/agents/probe/.well-known/agent-card.json")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.1)
                await delivered(client, "proxy.startup ready", 2)
        finally:
            monitoring.cancel()

    ServeHandler._monitor = monitor
    cli()
''', env, "serve", "--port", str(proxy_port), "--port-range-start", "40000",
        "--port-range-end", "41000", "--startup-timeout", "20")
    assert code == 0, output
    parent, = [event for event in events if event["message"] == "application.startup ready"]
    first, restarted = [event for event in events if event["message"] == "proxy.startup ready"]
    assert first["trace.id"] == parent["trace.id"]
    assert first["parent.span.id"] == parent["span.id"]
    assert restarted["trace.id"] and restarted["trace.id"] != parent["trace.id"]
    assert not restarted.get("parent.span.id")
    assert restarted["process.pid"] != first["process.pid"]
    _assert_attribution(restarted)
    died, = [event for event in events if event["message"].startswith("Proxy server died")]
    assert not died.get("trace.id")
    assert not any(event["message"].startswith(("Failed to start server", "AION system stopped"))
                   for event in events), output


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
