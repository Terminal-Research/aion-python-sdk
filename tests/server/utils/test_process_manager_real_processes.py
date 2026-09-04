"""Termination against real processes, with a real process tree below them.

The unit tests for ``ProcessManager`` patch ``os.killpg`` and assert the order
of the calls. That is the right level for the decision, and the wrong level for
the claim the decision exists to make: that nothing survives the teardown. A
mocked ``killpg`` cannot tell whether the child ever became a session leader,
whether the group id recorded for it is the group its children are actually in,
or whether a process that refuses ``SIGTERM`` is really killed.

These tests start real children that spawn real grandchildren, and then look
for the corpses. They are integration tests because they cost seconds and
POSIX signals rather than because they touch a database.
"""

from __future__ import annotations

import os
import subprocess
import signal
import sys
import time
from unittest.mock import MagicMock

import pytest

from aion.server.utils.processes.process_manager import ProcessManager

pytestmark = pytest.mark.integration

GRANDCHILD = [sys.executable, "-c", "import time; time.sleep(300)"]
"""A process the manager knows nothing about, started by the process it manages."""


def _spawn_a_grandchild_and_wait(conn) -> None:
    """Start a long-lived child of our own, report it, then idle.

    Runs in the managed process. This is the shape the teardown is meant to
    cover: an agent process that has started work of its own.
    """
    grandchild = subprocess.Popen(GRANDCHILD)
    conn.send(grandchild.pid)
    time.sleep(300)


def _ignore_termination_and_wait(conn) -> None:
    """Refuse ``SIGTERM`` and idle, so only ``SIGKILL`` can end this."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    conn.send(os.getpid())
    time.sleep(300)


def _alive(pid: int) -> bool:
    """Report whether a pid can still be signalled."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_gone(pid: int, timeout: float = 10.0) -> None:
    """Wait for a process to disappear, failing the test if it does not.

    Polled rather than waited on: these pids are not children of the test
    process, so there is nothing to ``wait`` for. A pid that has been signalled
    can also linger for a moment as a zombie under whichever process reaps it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f"Process {pid} was still alive after {timeout:.0f}s")


@pytest.fixture
def manager():
    """A manager whose processes are all stopped, however the test ends."""
    instance = ProcessManager(logger=MagicMock())
    try:
        yield instance
    finally:
        instance.shutdown_all(timeout=2.0)


@pytest.mark.skipif(os.name != "posix", reason="Process groups are POSIX only")
def test_stopping_a_managed_process_stops_what_it_started(manager) -> None:
    """The work a child spawned must not outlive the child.

    An orphaned agent process keeps running against a task whose lease this
    server no longer holds, which is the exact situation ownership exists to
    prevent - and the one that leaves no trace in the database.
    """
    assert manager.create_process(
        "agent", func=_spawn_a_grandchild_and_wait, use_pipe=True
    )
    grandchild_pid = manager.receive_from_process("agent", timeout=10)
    assert grandchild_pid is not None
    child_pid = manager.processes["agent"].pid
    assert _alive(grandchild_pid)

    assert manager.terminate_process("agent") is True

    _wait_until_gone(child_pid)
    _wait_until_gone(grandchild_pid)


@pytest.mark.skipif(os.name != "posix", reason="Process groups are POSIX only")
def test_a_process_that_refuses_to_stop_is_killed(manager) -> None:
    """The grace period ends in a signal that cannot be handled."""
    assert manager.create_process(
        "stubborn", func=_ignore_termination_and_wait, use_pipe=True
    )
    child_pid = manager.receive_from_process("stubborn", timeout=10)
    assert child_pid is not None

    assert manager.terminate_process("stubborn", timeout=1.0) is True

    _wait_until_gone(child_pid)


@pytest.mark.skipif(os.name != "posix", reason="Process groups are POSIX only")
def test_a_managed_process_leads_its_own_group(manager) -> None:
    """The recorded group id is the child's own, and its children share it.

    Everything else here rests on this: a group id that named the server's own
    group would turn the teardown into a signal to the whole server.
    """
    assert manager.create_process(
        "agent", func=_spawn_a_grandchild_and_wait, use_pipe=True
    )
    grandchild_pid = manager.receive_from_process("agent", timeout=10)
    child_pid = manager.processes["agent"].pid

    assert manager.processes["agent"].process_group_id == child_pid
    assert os.getpgid(child_pid) == child_pid
    assert os.getpgid(grandchild_pid) == child_pid
    assert os.getpgid(os.getpid()) != child_pid
