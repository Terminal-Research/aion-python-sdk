"""The serve monitor restarts a dead proxy while agents keep serving.

Focus areas:
  - A dead proxy is restarted through the callback, and a failed restart is
    tried again on the next check.
  - Monitoring ends when every agent has stopped, without restarting anything.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aion.cli.services.serve.monitoring import ServeMonitoringService


class _Processes:
    """Process manager whose processes die or start as a test scripts."""

    def __init__(self, **alive):
        self.alive = alive

    def cleanup_dead_processes(self):
        self.alive = {key: value for key, value in self.alive.items() if value}

    def get_process_info(self, key):
        if key not in self.alive:
            return None
        return SimpleNamespace(process=SimpleNamespace(is_alive=lambda: self.alive.get(key, False)))


@pytest.fixture
def monitor(monkeypatch):
    service = ServeMonitoringService()
    monkeypatch.setattr(service, "check_interval", 0)
    return service


async def test_dead_proxy_is_restarted_until_it_confirms(monitor):
    processes = _Processes(agent=True, proxy=False)
    attempts = []

    async def restart_proxy():
        attempts.append(len(attempts))
        if len(attempts) == 2:
            # Stop once the proxy is back: the agent ends the monitoring.
            processes.alive.update(proxy=True, agent=False)
            return True
        return False

    await monitor.execute(
        successful_agents=["agent"], proxy_started=True,
        process_manager=processes, restart_proxy=restart_proxy,
    )

    assert attempts == [0, 1]


async def test_monitoring_ends_without_restarts_when_every_agent_stopped(monitor):
    restart_proxy = AsyncMock(return_value=True)

    await monitor.execute(
        successful_agents=["agent"], proxy_started=True,
        process_manager=_Processes(agent=False, proxy=False), restart_proxy=restart_proxy,
    )

    restart_proxy.assert_not_awaited()


async def test_proxy_that_never_started_is_not_restarted(monitor):
    processes = _Processes(agent=True)
    checks = []

    def cleanup():
        checks.append(None)
        if len(checks) == 3:
            processes.alive["agent"] = False

    processes.cleanup_dead_processes = cleanup
    restart_proxy = AsyncMock(return_value=True)

    await monitor.execute(
        successful_agents=["agent"], proxy_started=False,
        process_manager=processes, restart_proxy=restart_proxy,
    )

    restart_proxy.assert_not_awaited()
