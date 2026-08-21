"""Tests for the in-process dispatch side of ``TaskTerminalListener``.

The database connection and the ``LISTEN`` subscription are exercised by the
integration suite, against a real PostgreSQL
(``tests/tasks/test_task_ownership_e2e_postgres.py``). What is safe and
useful to pin down without one is the pure asyncio bookkeeping: which waiter
wakes for which task id, and that registering, waiting, and releasing cannot
leak or cross-wire between unrelated tasks. ``_dispatch`` is called directly
here, standing in for what a real notification from the listen loop would
trigger.
"""

import asyncio

import pytest

from aion.server.tasks.notifications import TaskTerminalListener

SHORT_TIMEOUT = 0.05
"""Long enough for a same-loop wakeup, short enough not to slow the suite."""


@pytest.fixture
def listener() -> TaskTerminalListener:
    """A listener with no real database connection; ``_run`` is never started."""
    return TaskTerminalListener(db_manager=None)


async def test_a_dispatch_wakes_a_registered_waiter(listener) -> None:
    waiter = listener.register("task-1")

    listener._dispatch("task-1")

    assert await waiter.wait(timeout=SHORT_TIMEOUT) is True
    waiter.release()


async def test_wait_times_out_with_no_matching_dispatch(listener) -> None:
    waiter = listener.register("task-1")

    assert await waiter.wait(timeout=SHORT_TIMEOUT) is False
    waiter.release()


async def test_a_dispatch_for_a_different_task_does_not_wake_this_waiter(listener) -> None:
    waiter = listener.register("task-1")

    listener._dispatch("task-2")

    assert await waiter.wait(timeout=SHORT_TIMEOUT) is False
    waiter.release()


async def test_a_dispatch_with_no_registered_waiter_is_a_silent_no_op(listener) -> None:
    """Every ordinary terminal write reaches here; almost none has a waiter."""
    listener._dispatch("nobody-is-waiting")  # must not raise


async def test_two_registrations_for_the_same_task_share_one_event(listener) -> None:
    """Two callers racing the same cancellation both see the one notification."""
    first = listener.register("task-1")
    second = listener.register("task-1")

    listener._dispatch("task-1")

    assert await first.wait(timeout=SHORT_TIMEOUT) is True
    assert await second.wait(timeout=SHORT_TIMEOUT) is True
    first.release()
    second.release()


async def test_releasing_one_of_two_registrations_leaves_the_other_active(listener) -> None:
    first = listener.register("task-1")
    second = listener.register("task-1")

    first.release()
    listener._dispatch("task-1")

    assert await second.wait(timeout=SHORT_TIMEOUT) is True
    second.release()


async def test_the_waiter_map_is_empty_once_every_registration_is_released(listener) -> None:
    first = listener.register("task-1")
    second = listener.register("task-1")

    first.release()
    assert "task-1" in listener._waiters
    second.release()

    assert "task-1" not in listener._waiters
    assert "task-1" not in listener._waiter_refcounts


async def test_a_released_task_can_be_registered_again_with_a_fresh_wait(listener) -> None:
    """Release must not leave a stale, already-set event behind for a retry."""
    first = listener.register("task-1")
    listener._dispatch("task-1")
    assert await first.wait(timeout=SHORT_TIMEOUT) is True
    first.release()

    second = listener.register("task-1")

    assert await second.wait(timeout=SHORT_TIMEOUT) is False
    second.release()


async def test_stop_before_start_is_a_safe_no_op(listener) -> None:
    """Shutdown paths call ``stop`` unconditionally; it must tolerate this."""
    await listener.stop()  # never started; must not raise
