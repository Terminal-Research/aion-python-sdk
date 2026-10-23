"""Servers side by side on one machine, each on the ports it was given.

`aion serve --port P` puts the proxy on P, and the agents on ports from the
range ``--port-range-start`` and ``--port-range-end`` give - from P + 1 up
when no range is given. The harness gives every server the same range,
``AGENT_PORTS``, away from the proxy ports it takes from the kernel: macOS
picks those in sequence, so two servers created together get neighbouring
proxy ports, and agents placed from P + 1 up would take the second server's.
"""

from __future__ import annotations

import contextlib
import socket
from urllib.parse import urlsplit

import httpx
import pytest

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ServeProcess, ServeVariant
from tests.scenarios.harness.serve import AGENT_PORTS

pytestmark = pytest.mark.lifecycle

ATTEMPTS = 20
"""Tries at two neighbouring free ports before the scenario gives up on the machine."""


def _socket(held: contextlib.ExitStack) -> socket.socket:
    """A TCP socket closed with ``held``."""
    return held.enter_context(contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)))


def _neighbouring_ports() -> int:
    """The lower of two neighbouring free ports, the way macOS hands them to two servers created together.

    Both are asked of the kernel, as the harness asks for a proxy port: a
    kernel that picks in sequence, as macOS does, hands out neighbours and
    moves past them, so nothing that asks after this is given either. A
    kernel that picks at random gives two ports apart; the neighbour of the
    first is then bound by number.
    """
    for _ in range(ATTEMPTS):
        with contextlib.ExitStack() as held:
            lower, upper = _socket(held), _socket(held)
            lower.bind(("127.0.0.1", 0))
            upper.bind(("127.0.0.1", 0))
            port = int(lower.getsockname()[1])
            if upper.getsockname()[1] == port + 1:
                return port
            try:
                _socket(held).bind(("127.0.0.1", port + 1))
            except (OSError, OverflowError):
                continue
            return port
    raise RuntimeError(f"found no two neighbouring free ports in {ATTEMPTS} attempts")


def test_servers_on_neighbouring_ports_both_start(framework: Framework) -> None:
    """Two servers whose proxy ports are neighbours both start, each with its agents in the range it was given."""
    port = _neighbouring_ports()
    servers = [
        ServeProcess(framework, ServeVariant(), port=port),
        ServeProcess(framework, ServeVariant(), port=port + 1),
    ]
    try:
        for server in servers:
            server.start()
        health = [httpx.get(f"{server.base_url}/health/system/", timeout=5.0).json() for server in servers]
    finally:
        for server in servers:
            server.stop()

    agent_ports = [urlsplit(agent["url"]).port for payload in health for agent in payload["agents"].values()]
    assert agent_ports
    assert all(AGENT_PORTS[0] <= agent_port <= AGENT_PORTS[1] for agent_port in agent_ports), agent_ports
