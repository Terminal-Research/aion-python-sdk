"""Tests for ServeHandler's shutdown path.

Focus areas:
  - A shutdown signal ends monitoring without doing the teardown inside the
    signal handler, and without shutting down twice.
  - Background work started by ``run`` is referenced and its failures observed.
"""

import asyncio
import importlib.util
import signal
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _load_serve_module():
    """Load the real serve handler module, bypassing the suite's stubs.

    ``conftest`` replaces ``aion.cli.handlers`` wholesale with a stand-in — the
    CLI imports it at module load, and the sibling packages it depends on are
    not installed in this isolated environment. The module under test is
    therefore loaded straight from its file, with its collaborators stubbed for
    the duration of the import only: the previous ``sys.modules`` state is put
    back afterwards so the rest of the suite sees the real packages.

    Returns:
        The imported ``serve`` module.
    """
    service_names = (
        "EnvironmentContext",
        "ServeAgentStartupService",
        "ServeEnvironmentPreparerService",
        "ServeMonitoringService",
        "ServeProxyStartupService",
        "ServeShutdownService",
        "AionDeploymentRegisterVersionService",
    )
    stubs: dict[str, dict] = {
        "aion.cli.services": {name: MagicMock for name in service_names},
        "aion.server.utils.processes": {"ProcessManager": object},
        "aion.cli.utils.port_manager": {"AionPortManager": object},
        "aion.cli.utils.cli_messages": {
            "generate_welcome_message": lambda **_kwargs: ""
        },
    }

    saved = {name: sys.modules.get(name) for name in stubs}
    try:
        for name, attributes in stubs.items():
            module = types.ModuleType(name)
            module.__path__ = []  # a package, so submodule imports resolve
            for attribute, value in attributes.items():
                setattr(module, attribute, value)
            sys.modules[name] = module

        path = Path(__file__).resolve().parents[1] / "src/aion/cli/handlers/serve.py"
        spec = importlib.util.spec_from_file_location("serve_under_test", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


serve = _load_serve_module()
ServeHandler = serve.ServeHandler


@pytest.fixture
def handler():
    served = ServeHandler()
    served.process_manager = MagicMock()
    served.config = MagicMock()
    served.successful_agents = ["agent-1"]
    return served


class TestShutdownSignal:
    async def test_signal_only_records_the_request(self, handler):
        """The teardown must not run inside the handler: it blocks the loop for
        as long as the children take to die."""
        handler._request_shutdown(signal.SIGTERM)

        assert handler._shutdown_requested.is_set()
        handler.process_manager.shutdown_all.assert_not_called()

    async def test_monitor_returns_when_shutdown_is_requested(self, handler):
        """Monitoring blocks until the agents stop; a signal has to break it."""
        never = asyncio.Event()

        async def _forever(**_kwargs):
            await never.wait()

        with patch.object(serve, "ServeMonitoringService") as MockService:
            MockService.return_value.execute = _forever
            monitor = asyncio.ensure_future(handler._monitor())
            await asyncio.sleep(0)
            assert not monitor.done()

            handler._request_shutdown(signal.SIGINT)
            await asyncio.wait_for(monitor, timeout=1.0)

    async def test_monitor_returns_when_monitoring_finishes_on_its_own(self, handler):
        with patch.object(serve, "ServeMonitoringService") as MockService:
            MockService.return_value.execute = AsyncMock(return_value=None)
            await asyncio.wait_for(handler._monitor(), timeout=1.0)

    async def test_monitor_surfaces_a_monitoring_failure(self, handler):
        with patch.object(serve, "ServeMonitoringService") as MockService:
            MockService.return_value.execute = AsyncMock(
                side_effect=RuntimeError("monitor blew up")
            )
            with pytest.raises(RuntimeError, match="monitor blew up"):
                await handler._monitor()

    async def test_monitor_before_startup_is_an_error(self):
        with pytest.raises(RuntimeError):
            await ServeHandler()._monitor()

    async def test_handlers_are_installed_on_the_running_loop(self, handler):
        """Installed on the loop, not via signal.signal, so the callback runs as
        a loop callback rather than between bytecodes."""
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler") as add_handler:
            handler._setup_signal_handlers()

        installed = {call.args[0] for call in add_handler.call_args_list}
        assert installed == {signal.SIGINT, signal.SIGTERM}

    async def test_falls_back_when_the_loop_cannot_install_handlers(self, handler):
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", side_effect=NotImplementedError):
            with patch.object(serve.signal, "signal") as raw_signal:
                handler._setup_signal_handlers()

        assert raw_signal.call_count == 2


class TestBackgroundTasks:
    async def test_task_is_referenced_until_it_finishes(self, handler):
        started = asyncio.Event()

        async def _work():
            started.set()
            await asyncio.sleep(0.01)

        task = asyncio.ensure_future(_work())
        handler._track_background_task(task, "work")

        await started.wait()
        assert task in handler._background_tasks

        await task
        await asyncio.sleep(0)
        assert task not in handler._background_tasks

    async def test_failure_is_reported_not_swallowed(self, handler):
        async def _boom():
            raise ConnectionError("aion api unreachable")

        task = asyncio.ensure_future(_boom())
        with patch.object(serve, "logger") as mock_logger:
            handler._track_background_task(task, "deployment version registration")
            with pytest.raises(ConnectionError):
                await task
            await asyncio.sleep(0)

        assert mock_logger.warning.called
        message = mock_logger.warning.call_args.args[0]
        assert "deployment version registration" in message

    async def test_cancelled_task_is_not_reported_as_a_failure(self, handler):
        task = asyncio.ensure_future(asyncio.sleep(10))
        with patch.object(serve, "logger") as mock_logger:
            handler._track_background_task(task, "work")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)

        assert not mock_logger.warning.called
