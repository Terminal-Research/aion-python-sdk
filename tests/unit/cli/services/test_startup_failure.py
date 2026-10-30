"""A failure reply must not let the launcher kill logs that are still queued."""

import asyncio
import logging
import signal
from unittest.mock import Mock

import pytest

from aion.cli.services.serve.agent_startup import ServeAgentStartupService
from aion.cli.services.serve.proxy_startup import ServeProxyStartupService


@pytest.mark.parametrize("role", ["agent", "proxy"])
def test_child_drains_failure_logs_before_notifying_the_launcher(monkeypatch, role):
    import aion.server
    import aion.server.logging
    import aion.proxy

    order = []
    monkeypatch.setattr(logging, "shutdown", lambda: order.append("logs drained"))
    monkeypatch.setattr(aion.server.logging, "setup_root_logger", lambda: None)
    monkeypatch.setattr(signal, "signal", lambda *args: None)
    monkeypatch.setattr(asyncio, "new_event_loop", Mock(return_value=Mock()))
    monkeypatch.setattr(asyncio, "set_event_loop", lambda loop: None)
    conn = Mock()
    conn.send.side_effect = lambda message: order.append("failure reported")

    def fail(*args, **kwargs):
        raise RuntimeError("startup failed before serving")

    monkeypatch.setattr(aion.server, "run_server", fail)
    monkeypatch.setattr(aion.proxy, "AionAgentProxyServer", fail)
    with pytest.raises(RuntimeError, match="startup failed before serving"):
        if role == "agent":
            ServeAgentStartupService._agent_wrapper("probe", Mock(), 8000, None, conn)
        else:
            ServeProxyStartupService._proxy_wrapper(8000, {}, conn=conn)
    assert order == ["logs drained", "failure reported"]
