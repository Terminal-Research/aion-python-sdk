"""Tests for the in-process dispatch side of ``TaskEventListener``.

The database connection and the ``LISTEN`` subscription are exercised by the
integration suite, against a real PostgreSQL
(``tests/tasks/test_task_ownership_e2e_postgres.py``). What is safe and
useful to pin down without one is the pure asyncio bookkeeping: which waiter
or subscriber wakes for which ``(kind, task_id)``, that registering, waiting,
and releasing cannot leak or cross-wire between unrelated tasks or kinds, and
that an unrecognized payload is skipped rather than raised. ``_dispatch`` is
called directly here, standing in for what a real notification from the
listen loop would trigger.
"""

import asyncio

import pytest

from aion.db.postgres.events import TaskEvent, TaskEventKind
from aion.server.tasks.notifications import TaskEventListener

SHORT_TIMEOUT = 0.05
"""Long enough for a same-loop wakeup, short enough not to slow the suite."""

CANCEL_REQUESTED = TaskEventKind.CANCEL_REQUESTED
CANCEL_RESOLVED = TaskEventKind.CANCEL_RESOLVED


def payload(kind: TaskEventKind, task_id: str) -> str:
    return TaskEvent(kind=kind, task_id=task_id).model_dump_json()


@pytest.fixture
def listener() -> TaskEventListener:
    """A listener with no real database connection; ``_run`` is never started."""
    return TaskEventListener(db_manager=None)


# -- register/wait ------------------------------------------------------


async def test_a_dispatch_wakes_a_registered_waiter(listener) -> None:
    waiter = listener.register(CANCEL_RESOLVED, "task-1")

    listener._dispatch(payload(CANCEL_RESOLVED, "task-1"))

    assert await waiter.wait(timeout=SHORT_TIMEOUT) is True
    waiter.release()


async def test_wait_times_out_with_no_matching_dispatch(listener) -> None:
    waiter = listener.register(CANCEL_RESOLVED, "task-1")

    assert await waiter.wait(timeout=SHORT_TIMEOUT) is False
    waiter.release()


async def test_a_dispatch_for_a_different_task_does_not_wake_this_waiter(listener) -> None:
    waiter = listener.register(CANCEL_RESOLVED, "task-1")

    listener._dispatch(payload(CANCEL_RESOLVED, "task-2"))

    assert await waiter.wait(timeout=SHORT_TIMEOUT) is False
    waiter.release()


async def test_a_dispatch_of_a_different_kind_does_not_wake_this_waiter(listener) -> None:
    """Same task id, wrong kind - the two channels-in-one must not cross-wire."""
    waiter = listener.register(CANCEL_RESOLVED, "task-1")

    listener._dispatch(payload(CANCEL_REQUESTED, "task-1"))

    assert await waiter.wait(timeout=SHORT_TIMEOUT) is False
    waiter.release()


async def test_a_dispatch_with_no_registered_waiter_is_a_silent_no_op(listener) -> None:
    """Every ordinary terminal write reaches here; almost none has a waiter."""
    listener._dispatch(payload(CANCEL_RESOLVED, "nobody-is-waiting"))  # must not raise


async def test_two_registrations_for_the_same_key_share_one_event(listener) -> None:
    """Two callers racing the same cancellation both see the one notification."""
    first = listener.register(CANCEL_RESOLVED, "task-1")
    second = listener.register(CANCEL_RESOLVED, "task-1")

    listener._dispatch(payload(CANCEL_RESOLVED, "task-1"))

    assert await first.wait(timeout=SHORT_TIMEOUT) is True
    assert await second.wait(timeout=SHORT_TIMEOUT) is True
    first.release()
    second.release()


async def test_releasing_one_of_two_registrations_leaves_the_other_active(listener) -> None:
    first = listener.register(CANCEL_RESOLVED, "task-1")
    second = listener.register(CANCEL_RESOLVED, "task-1")

    first.release()
    listener._dispatch(payload(CANCEL_RESOLVED, "task-1"))

    assert await second.wait(timeout=SHORT_TIMEOUT) is True
    second.release()


async def test_the_waiter_map_is_empty_once_every_registration_is_released(listener) -> None:
    first = listener.register(CANCEL_RESOLVED, "task-1")
    second = listener.register(CANCEL_RESOLVED, "task-1")
    key = (CANCEL_RESOLVED, "task-1")

    first.release()
    assert key in listener._waiters
    second.release()

    assert key not in listener._waiters
    assert key not in listener._waiter_refcounts


async def test_a_released_task_can_be_registered_again_with_a_fresh_wait(listener) -> None:
    """Release must not leave a stale, already-set event behind for a retry."""
    first = listener.register(CANCEL_RESOLVED, "task-1")
    listener._dispatch(payload(CANCEL_RESOLVED, "task-1"))
    assert await first.wait(timeout=SHORT_TIMEOUT) is True
    first.release()

    second = listener.register(CANCEL_RESOLVED, "task-1")

    assert await second.wait(timeout=SHORT_TIMEOUT) is False
    second.release()


# -- subscribe ------------------------------------------------------------


async def test_a_dispatch_invokes_a_subscriber_for_a_matching_kind(listener) -> None:
    seen = []
    listener.subscribe((CANCEL_REQUESTED,), lambda kind, task_id: seen.append((kind, task_id)))

    listener._dispatch(payload(CANCEL_REQUESTED, "task-1"))

    assert seen == [(CANCEL_REQUESTED, "task-1")]


async def test_a_dispatch_does_not_invoke_a_subscriber_for_a_different_kind(listener) -> None:
    seen = []
    listener.subscribe((CANCEL_REQUESTED,), lambda kind, task_id: seen.append((kind, task_id)))

    listener._dispatch(payload(CANCEL_RESOLVED, "task-1"))

    assert seen == []


async def test_a_subscription_can_cover_more_than_one_kind(listener) -> None:
    seen = []
    listener.subscribe(
        (CANCEL_REQUESTED, CANCEL_RESOLVED), lambda kind, task_id: seen.append((kind, task_id))
    )

    listener._dispatch(payload(CANCEL_REQUESTED, "task-1"))
    listener._dispatch(payload(CANCEL_RESOLVED, "task-2"))

    assert seen == [(CANCEL_REQUESTED, "task-1"), (CANCEL_RESOLVED, "task-2")]


async def test_subscribe_rejects_an_empty_kind_collection(listener) -> None:
    with pytest.raises(ValueError):
        listener.subscribe((), lambda kind, task_id: None)


async def test_a_canceled_subscription_stops_receiving_events(listener) -> None:
    seen = []
    subscription = listener.subscribe(
        (CANCEL_REQUESTED,), lambda kind, task_id: seen.append((kind, task_id))
    )

    subscription.cancel()
    listener._dispatch(payload(CANCEL_REQUESTED, "task-1"))

    assert seen == []


async def test_two_subscribers_to_the_same_kind_both_run(listener) -> None:
    first_seen = []
    second_seen = []
    listener.subscribe((CANCEL_REQUESTED,), lambda kind, task_id: first_seen.append(task_id))
    listener.subscribe((CANCEL_REQUESTED,), lambda kind, task_id: second_seen.append(task_id))

    listener._dispatch(payload(CANCEL_REQUESTED, "task-1"))

    assert first_seen == ["task-1"]
    assert second_seen == ["task-1"]


async def test_a_subscriber_that_raises_does_not_stop_the_others(listener) -> None:
    seen = []

    def boom(kind, task_id):
        raise RuntimeError("subscriber failed")

    listener.subscribe((CANCEL_REQUESTED,), boom)
    listener.subscribe((CANCEL_REQUESTED,), lambda kind, task_id: seen.append(task_id))

    listener._dispatch(payload(CANCEL_REQUESTED, "task-1"))  # must not raise

    assert seen == ["task-1"]


async def test_a_waiter_and_a_subscriber_both_fire_from_one_dispatch(listener) -> None:
    seen = []
    listener.subscribe((CANCEL_REQUESTED,), lambda kind, task_id: seen.append(task_id))
    waiter = listener.register(CANCEL_REQUESTED, "task-1")

    listener._dispatch(payload(CANCEL_REQUESTED, "task-1"))

    assert seen == ["task-1"]
    assert await waiter.wait(timeout=SHORT_TIMEOUT) is True
    waiter.release()


# -- malformed payloads -----------------------------------------------------


async def test_an_unparseable_payload_is_skipped_not_raised(listener) -> None:
    listener._dispatch("not json at all")  # must not raise


async def test_an_unrecognized_kind_is_skipped_not_raised(listener) -> None:
    """Forward compatibility: a newer deployment's kind must not crash an older one."""
    listener._dispatch('{"kind": "some_future_kind", "task_id": "task-1"}')  # must not raise


async def test_stop_before_start_is_a_safe_no_op(listener) -> None:
    """Shutdown paths call ``stop`` unconditionally; it must tolerate this."""
    await listener.stop()  # never started; must not raise
