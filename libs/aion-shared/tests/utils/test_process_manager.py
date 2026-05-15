"""Tests for ProcessManager.

Focus areas:
  - create_process: basic, duplicate key guard, with pipe (func_kwargs injection)
  - terminate_process: not found, already dead, graceful, force-kill fallback
  - remove_process: not found, running process removal
  - get_process_info: found / not found
  - send_to_process / receive_from_process: pipe path and error paths
  - get_connection: found / not found
  - list_processes: status refresh (running -> stopped for dead processes)
  - cleanup_dead_processes: removes dead, leaves alive
  - shutdown_all: terminates all, clears dict
  - restart_process: not found, successful restart
  - context manager: __exit__ triggers shutdown_all
"""

import time
from multiprocessing.connection import Connection
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from aion.shared.utils.processes.process_manager import (
    ProcessInfo,
    ProcessManager,
    ProcessStatus,
)


def _manager() -> ProcessManager:
    return ProcessManager(logger=MagicMock())


def _mock_process(alive: bool = True) -> MagicMock:
    p = MagicMock()
    p.is_alive.return_value = alive
    p.pid = 12345
    return p


def _make_process_info(key: str = "p1", alive: bool = True) -> ProcessInfo:
    return ProcessInfo(
        key=key,
        process=_mock_process(alive=alive),
        target_function=lambda: None,
        args=(),
        kwargs={},
        created_at=time.time(),
        status=ProcessStatus.RUNNING,
        pid=12345,
    )


class TestCreateProcess:
    def test_creates_process_with_key(self):
        """Verify that creates process with key."""
        mgr = _manager()
        with patch("multiprocessing.Process") as MockProc:
            mock_p = _mock_process()
            MockProc.return_value = mock_p

            result = mgr.create_process("p1", func=lambda: None)

        assert result is True
        assert "p1" in mgr.processes

    def test_returns_false_for_duplicate_key(self):
        """Verify that returns false for duplicate key."""
        mgr = _manager()
        mgr.processes["p1"] = _make_process_info("p1")

        result = mgr.create_process("p1", func=lambda: None)
        assert result is False

    def test_pipe_injects_conn_kwarg(self):
        """Verify that pipe injects conn kwarg."""
        mgr = _manager()
        captured_kwargs: dict[str, Any] = {}

        def _fn(**kwargs):
            captured_kwargs.update(kwargs)

        with patch("multiprocessing.Process") as MockProc:
            with patch("multiprocessing.Pipe") as MockPipe:
                parent = MagicMock(spec=Connection)
                child = MagicMock(spec=Connection)
                MockPipe.return_value = (parent, child)
                mock_p = _mock_process()
                MockProc.return_value = mock_p

                result = mgr.create_process("p1", func=_fn, use_pipe=True)

        assert result is True
        info = mgr.processes["p1"]
        assert info.parent_conn is parent
        assert info.child_conn is child
        assert info.kwargs.get("conn") is child

    def test_process_info_stored_correctly(self):
        """Verify that process info stored correctly."""
        mgr = _manager()
        fn = lambda: None

        with patch("multiprocessing.Process") as MockProc:
            mock_p = _mock_process()
            MockProc.return_value = mock_p

            mgr.create_process("p1", func=fn, func_args=(1, 2), func_kwargs={"x": 3})

        info = mgr.processes["p1"]
        assert info.key == "p1"
        assert info.target_function is fn
        assert info.args == (1, 2)
        assert info.kwargs == {"x": 3}
        assert info.status == ProcessStatus.RUNNING

    def test_returns_false_on_exception(self):
        """Verify that returns false on exception."""
        mgr = _manager()
        with patch("multiprocessing.Process", side_effect=OSError("fork failed")):
            result = mgr.create_process("p1", func=lambda: None)
        assert result is False


class TestTerminateProcess:
    def test_returns_false_for_unknown_key(self):
        """Verify that returns false for unknown key."""
        mgr = _manager()
        result = mgr.terminate_process("unknown")
        assert result is False

    def test_already_dead_process_marked_stopped(self):
        """Verify that already dead process marked stopped."""
        mgr = _manager()
        info = _make_process_info("p1", alive=False)
        mgr.processes["p1"] = info

        result = mgr.terminate_process("p1")

        assert result is True
        assert info.status == ProcessStatus.STOPPED

    def test_graceful_termination(self):
        """Verify that graceful termination."""
        mgr = _manager()
        process = _mock_process(alive=True)
        process.join.side_effect = lambda timeout=None: setattr(process, "_alive", False)
        process.is_alive.side_effect = [True, False]  # alive before, dead after join

        info = _make_process_info("p1")
        info.process = process
        mgr.processes["p1"] = info

        result = mgr.terminate_process("p1")

        assert result is True
        process.terminate.assert_called_once()
        assert info.status == ProcessStatus.TERMINATED

    def test_force_kill_when_still_alive_after_terminate(self):
        """Verify that force kill when still alive after terminate."""
        mgr = _manager()
        process = _mock_process()
        # Still alive after terminate+join
        process.is_alive.side_effect = [True, True, False]

        info = _make_process_info("p1")
        info.process = process
        mgr.processes["p1"] = info

        result = mgr.terminate_process("p1")

        assert result is True
        process.kill.assert_called_once()

    def test_closes_pipe_connections(self):
        """Verify that closes pipe connections."""
        mgr = _manager()
        process = _mock_process()
        process.is_alive.return_value = False

        parent_conn = MagicMock(spec=Connection)
        child_conn = MagicMock(spec=Connection)
        info = _make_process_info("p1", alive=False)
        info.parent_conn = parent_conn
        info.child_conn = child_conn
        info.process = process
        mgr.processes["p1"] = info

        mgr.terminate_process("p1")

        parent_conn.close.assert_called_once()
        child_conn.close.assert_called_once()


class TestRemoveProcess:
    def test_returns_false_for_unknown_key(self):
        """Verify that returns false for unknown key."""
        mgr = _manager()
        assert mgr.remove_process("nope") is False

    def test_removes_dead_process(self):
        """Verify that removes dead process."""
        mgr = _manager()
        info = _make_process_info("p1", alive=False)
        mgr.processes["p1"] = info

        result = mgr.remove_process("p1")

        assert result is True
        assert "p1" not in mgr.processes

    def test_terminates_and_removes_live_process(self):
        """Verify that terminates and removes live process."""
        mgr = _manager()
        process = _mock_process(alive=True)
        process.is_alive.side_effect = [True, False]
        info = _make_process_info("p1")
        info.process = process
        mgr.processes["p1"] = info

        result = mgr.remove_process("p1")

        assert result is True
        assert "p1" not in mgr.processes


class TestGetProcessInfo:
    def test_returns_info_for_known_key(self):
        """Verify that returns info for known key."""
        mgr = _manager()
        info = _make_process_info("p1")
        mgr.processes["p1"] = info
        assert mgr.get_process_info("p1") is info

    def test_returns_none_for_unknown_key(self):
        """Verify that returns none for unknown key."""
        mgr = _manager()
        assert mgr.get_process_info("nope") is None


class TestPipeCommunication:
    def test_send_returns_false_for_unknown_key(self):
        """Verify that send returns false for unknown key."""
        mgr = _manager()
        assert mgr.send_to_process("nope", "msg") is False

    def test_send_returns_false_without_pipe(self):
        """Verify that send returns false without pipe."""
        mgr = _manager()
        mgr.processes["p1"] = _make_process_info("p1")
        assert mgr.send_to_process("p1", "msg") is False

    def test_send_returns_true_with_pipe(self):
        """Verify that send returns true with pipe."""
        mgr = _manager()
        conn = MagicMock(spec=Connection)
        info = _make_process_info("p1")
        info.parent_conn = conn
        mgr.processes["p1"] = info

        result = mgr.send_to_process("p1", {"data": 42})

        assert result is True
        conn.send.assert_called_once_with({"data": 42})

    def test_receive_returns_none_for_unknown_key(self):
        """Verify that receive returns none for unknown key."""
        mgr = _manager()
        assert mgr.receive_from_process("nope") is None

    def test_receive_returns_none_without_pipe(self):
        """Verify that receive returns none without pipe."""
        mgr = _manager()
        mgr.processes["p1"] = _make_process_info("p1")
        assert mgr.receive_from_process("p1") is None

    def test_receive_blocking_returns_message(self):
        """Verify that receive blocking returns message."""
        mgr = _manager()
        conn = MagicMock(spec=Connection)
        conn.recv.return_value = "hello"
        info = _make_process_info("p1")
        info.parent_conn = conn
        mgr.processes["p1"] = info

        msg = mgr.receive_from_process("p1")
        assert msg == "hello"

    def test_receive_with_timeout_polls(self):
        """Verify that receive with timeout polls."""
        mgr = _manager()
        conn = MagicMock(spec=Connection)
        conn.poll.return_value = True
        conn.recv.return_value = "data"
        info = _make_process_info("p1")
        info.parent_conn = conn
        mgr.processes["p1"] = info

        msg = mgr.receive_from_process("p1", timeout=1.0)

        conn.poll.assert_called_once_with(1.0)
        assert msg == "data"

    def test_receive_with_timeout_returns_none_on_poll_miss(self):
        """Verify that receive with timeout returns none on poll miss."""
        mgr = _manager()
        conn = MagicMock(spec=Connection)
        conn.poll.return_value = False
        info = _make_process_info("p1")
        info.parent_conn = conn
        mgr.processes["p1"] = info

        assert mgr.receive_from_process("p1", timeout=0.1) is None


class TestGetConnection:
    def test_returns_none_for_unknown_key(self):
        """Verify that returns none for unknown key."""
        mgr = _manager()
        assert mgr.get_connection("nope") is None

    def test_returns_none_without_pipe(self):
        """Verify that returns none without pipe."""
        mgr = _manager()
        mgr.processes["p1"] = _make_process_info("p1")
        assert mgr.get_connection("p1") is None

    def test_returns_parent_conn(self):
        """Verify that returns parent conn."""
        mgr = _manager()
        conn = MagicMock(spec=Connection)
        info = _make_process_info("p1")
        info.parent_conn = conn
        mgr.processes["p1"] = info
        assert mgr.get_connection("p1") is conn


class TestListProcesses:
    def test_empty_manager_returns_empty_dict(self):
        """Verify that empty manager returns empty dict."""
        mgr = _manager()
        assert mgr.list_processes() == {}

    def test_running_process_status_is_running(self):
        """Verify that running process status is running."""
        mgr = _manager()
        process = _mock_process(alive=True)
        info = _make_process_info("p1")
        info.process = process
        mgr.processes["p1"] = info

        result = mgr.list_processes()

        assert result["p1"]["status"] == "running"
        assert result["p1"]["is_alive"] is True

    def test_dead_process_status_updated_to_stopped(self):
        """Verify that dead process status updated to stopped."""
        mgr = _manager()
        process = _mock_process(alive=False)
        info = _make_process_info("p1")
        info.process = process
        info.status = ProcessStatus.RUNNING  # stale status
        mgr.processes["p1"] = info

        result = mgr.list_processes()

        assert result["p1"]["status"] == "stopped"

    def test_list_contains_function_name(self):
        """Verify that list contains function name."""
        def my_worker():
            pass

        mgr = _manager()
        process = _mock_process(alive=True)
        info = _make_process_info("p1")
        info.target_function = my_worker
        info.process = process
        mgr.processes["p1"] = info

        result = mgr.list_processes()
        assert result["p1"]["function_name"] == "my_worker"


class TestCleanupDeadProcesses:
    def test_removes_dead_processes(self):
        """Verify that removes dead processes."""
        mgr = _manager()
        dead_info = _make_process_info("dead", alive=False)
        alive_info = _make_process_info("alive", alive=True)
        mgr.processes["dead"] = dead_info
        mgr.processes["alive"] = alive_info

        count = mgr.cleanup_dead_processes()

        assert count == 1
        assert "dead" not in mgr.processes
        assert "alive" in mgr.processes

    def test_returns_zero_when_all_alive(self):
        """Verify that returns zero when all alive."""
        mgr = _manager()
        mgr.processes["p1"] = _make_process_info("p1", alive=True)
        assert mgr.cleanup_dead_processes() == 0

    def test_returns_zero_for_empty_manager(self):
        """Verify that returns zero for empty manager."""
        mgr = _manager()
        assert mgr.cleanup_dead_processes() == 0


class TestShutdownAll:
    def test_shuts_down_all_processes(self):
        """Verify that shuts down all processes."""
        mgr = _manager()
        for key in ("p1", "p2", "p3"):
            info = _make_process_info(key, alive=False)
            mgr.processes[key] = info

        result = mgr.shutdown_all()

        assert result is True
        assert len(mgr.processes) == 0

    def test_returns_false_if_any_terminate_fails(self):
        """Verify that returns false if any terminate fails."""
        mgr = _manager()
        # Inject a process that will fail to terminate
        bad_process = _mock_process(alive=True)
        bad_process.terminate.side_effect = OSError("permission denied")
        info = _make_process_info("p1")
        info.process = bad_process
        mgr.processes["p1"] = info

        result = mgr.shutdown_all()
        assert result is False

    def test_clears_processes_dict(self):
        """Verify that clears processes dict."""
        mgr = _manager()
        mgr.processes["p1"] = _make_process_info("p1", alive=False)
        mgr.shutdown_all()
        assert mgr.processes == {}


class TestRestartProcess:
    def test_returns_false_for_unknown_key(self):
        """Verify that returns false for unknown key."""
        mgr = _manager()
        assert mgr.restart_process("nope") is False

    def test_successful_restart(self):
        """Verify that successful restart."""
        mgr = _manager()

        fn = lambda: None
        info = _make_process_info("p1", alive=False)
        info.target_function = fn
        mgr.processes["p1"] = info

        with patch("multiprocessing.Process") as MockProc:
            mock_p = _mock_process()
            MockProc.return_value = mock_p

            result = mgr.restart_process("p1")

        assert result is True
        assert "p1" in mgr.processes


class TestContextManager:
    def test_exit_calls_shutdown_all(self):
        """Verify that exit calls shutdown all."""
        mgr = _manager()
        mgr.shutdown_all = MagicMock(return_value=True)

        with mgr:
            pass

        mgr.shutdown_all.assert_called_once()

    def test_exit_on_exception_still_shuts_down(self):
        """Verify that exit on exception still shuts down."""
        mgr = _manager()
        mgr.shutdown_all = MagicMock(return_value=True)

        try:
            with mgr:
                raise ValueError("oops")
        except ValueError:
            pass

        mgr.shutdown_all.assert_called_once()
