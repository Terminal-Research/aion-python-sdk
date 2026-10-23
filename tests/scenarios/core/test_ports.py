"""Servers side by side on one machine, each on the ports it was given.

`aion serve --port P` puts the proxy on P, and the agents on ports from the
range ``--port-range-start`` and ``--port-range-end`` give - from P + 1 up
when no range is given. The harness hands out proxy ports in sequence
(``proxy_port``), so two servers created together get neighbouring proxy
ports, and agents placed from P + 1 up would take the second server's. It
gives every server the same agent range, ``AGENT_PORTS``, apart from them.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ServeProcess, ServeVariant
from tests.scenarios.harness.serve import AGENT_PORTS, proxy_port

pytestmark = pytest.mark.lifecycle


def test_servers_on_neighbouring_ports_both_start(framework: Framework) -> None:
    """Two servers whose proxy ports are neighbours both start, each with its agents in the range it was given."""
    port = proxy_port(neighbours=2)
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
