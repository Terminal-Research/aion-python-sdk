"""Two instances over one database, through the layers a request goes through.

The focused tests stub either side of the boundary: the route tests give the
registry a stub provider and an in-memory store, and the port tests drive the
provider with no registry above it. Neither can show that the assembled thing
works, and the assembly is where a distributed mistake lives - a lease acquired
against one identifier and a row written against another, or enforcement that
is switched on in a component nobody wired up.

Each ``_Instance`` here is one server process: its own registry, its own
provider, its own store, one shared PostgreSQL. Skipped unless
``POSTGRES_TEST_URL`` names a database that may be migrated and truncated.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from unittest.mock import Mock

import pytest
import pytest_asyncio

from .postgres_support import (
    POSTGRES_TEST_URL,
    claim_count,
    expire_all,
    prepared_database,
    provider as _provider,
    short_lease,
    task_state,
    truncate,
    write_task,
)

from a2a.server.tasks import TaskUpdater
from a2a.types import CancelTaskRequest, SubscribeToTaskRequest, Task, TaskState
from a2a.utils.errors import TaskNotCancelableError
from sqlalchemy import text

from aion.server.agent.execution.scope import init_execution_scope
from aion.server.core.app.handlers import request_handler as _request_handler_module
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.notifications import TaskEventListener
from aion.server.tasks.ownership import (
    PostgresOwnershipProvider,
    TaskOwnershipBusy,
    TaskOwnershipLost,
)
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore
from .postgres_support import db_manager as _db_manager

STREAM_TIMEOUT_SECONDS = 10.0
"""How long a resubscribe may take before the test calls it a hang."""


class _GracefulAgentExecutor:
    """Stands in for the real ``AionAgentRequestExecutor`` where cancel matters.

    A plain ``Mock`` works for every other test here because nothing drives
    its ``execute`` to completion: ``create_task_if_missing=True`` alone never
    enqueues a request, so the producer sits blocked on an empty queue and
    ``execute`` is never called. Cancellation is different - cross-pod
    cancellation now depends on the owner's own ``ActiveTask.cancel`` actually
    publishing a terminal event through ``TaskUpdater``, the same way the real
    executor does, or the waiting side has nothing to ever wake up for.
    """

    async def execute(self, context, event_queue) -> None:
        raise NotImplementedError("not exercised by these tests")

    async def cancel(self, context, event_queue) -> None:
        """Publish the terminal CANCELED event, exactly as a real cancel does."""
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _migrated_database():
    """Migrate once and keep one initialized manager for the whole module."""
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def database(_migrated_database):
    """Hand each test an empty claim and task table."""
    await truncate()
    return _migrated_database


@dataclass
class _Instance:
    """One server process: its request handler, its provider, its store."""

    name: str
    provider: PostgresOwnershipProvider
    store: PostgresTaskStore
    handler: AionRequestHandler
    listener: TaskEventListener

    @property
    def registry(self):
        """The registry the handler built for itself."""
        return self.handler._active_task_registry

    async def execute(self, task_id: str):
        """Take the task on, the way an inbound message does."""
        # The scope is per request and normally opened by the server
        # middleware; the registry writes the task manager into it.
        init_execution_scope()
        return await self.registry.get_or_create(
            task_id,
            call_context=Mock(),
            create_task_if_missing=True,
        )

    async def subscribe(self, task_id: str) -> list:
        """Resubscribe to the task and read the whole stream out.

        Bounded on purpose. The failure this guards against is a stream that
        stays open with nothing to send - an execution built for a task this
        instance cannot run - and an unbounded read would answer that by
        hanging the suite rather than by failing it.
        """
        init_execution_scope()

        async def read() -> list:
            """Drain the resubscribe stream."""
            return [
                event
                async for event in self.handler.on_subscribe_to_task(
                    SubscribeToTaskRequest(id=task_id),
                    Mock(),
                )
            ]

        return await asyncio.wait_for(read(), timeout=STREAM_TIMEOUT_SECONDS)

    async def cancel(self, task_id: str):
        """Cancel the task, the way a control request does."""
        return await self.handler.on_cancel_task(
            CancelTaskRequest(id=task_id),
            Mock(),
        )

    async def aclose(self) -> None:
        """Shut the instance down, releasing whatever it still holds."""
        await self.registry.aclose()
        await self.listener.stop()


def _instance(name: str, *, enable_push: bool = True, **provider_kwargs) -> _Instance:
    """Assemble one instance over the shared database.

    The store and the provider are the pair ``StoreManager`` selects together,
    and the handler is given that same provider, which is how a deployed
    process is built. The agent executor is ``_GracefulAgentExecutor`` rather
    than a plain ``Mock`` because a cross-pod cancellation genuinely depends
    on it publishing a terminal event, and the event listener is real and
    started immediately - both needed for
    ``test_cancelling_from_another_instance_stops_the_holder`` to observe the
    real ``LISTEN``/``NOTIFY`` path rather than only its fencing logic.

    The listener is built before the provider and threaded into it
    (``event_listener=``), exactly as ``StoreManager.initialize`` orders it -
    the provider subscribes to ``CANCEL_REQUESTED`` from its own constructor,
    so it must already exist.

    Args:
        enable_push: Whether this instance's *provider* subscribes to
            ``CANCEL_REQUESTED`` at all. The handler still gets the listener
            regardless - an instance always needs it to wait on
            ``CANCEL_RESOLVED`` when it acts as a controller - this only
            controls whether the instance, when it holds a task, discovers a
            cancellation request over the fast path or purely by its own
            heartbeat renewal. ``False`` is what a test reaches for when it
            needs the pre-push, poll-only timing back, deterministically -
            see ``test_cancel_wait_timeout_still_reports_current_state_and_converges_later``.
    """
    listener = TaskEventListener(db_manager=_db_manager)
    listener.start()
    provider = _provider(
        name, event_listener=listener if enable_push else None, **provider_kwargs
    )
    store = PostgresTaskStore(agent_id=provider.agent_id, ownership_provider=provider)
    agent_card = Mock()
    agent_card.capabilities.streaming = True
    handler = AionRequestHandler(
        agent_executor=_GracefulAgentExecutor(),
        task_store=store,
        agent_card=agent_card,
        ownership_provider=provider,
        event_listener=listener,
    )
    return _Instance(name=name, provider=provider, store=store, handler=handler, listener=listener)


@pytest_asyncio.fixture(loop_scope="module")
async def instances(database):
    """Two instances that are closed however the test ends."""
    built: list[_Instance] = []

    def build(name: str, **provider_kwargs) -> _Instance:
        """Register one instance for shutdown and hand it to the test."""
        instance = _instance(name, **provider_kwargs)
        built.append(instance)
        return instance

    try:
        yield build
    finally:
        for instance in built:
            await instance.aclose()


async def test_only_the_first_instance_takes_the_task_on(instances) -> None:
    """The refusal names the holder, and no execution is built behind it."""
    task_id = str(uuid.uuid4())
    first, second = instances("pod-a"), instances("pod-b")

    await first.execute(task_id)

    with pytest.raises(TaskOwnershipBusy) as refused:
        await second.execute(task_id)

    assert refused.value.owner_instance_id == "pod-a"
    assert refused.value.data["task_id"] == task_id
    assert second.registry._active_tasks == {}
    assert first.provider.claim_for(task_id) is not None
    assert await claim_count() == 1


async def test_the_holder_writes_and_the_other_instance_cannot(instances) -> None:
    """The lease the registry took is the token the store writes under."""
    task_id = str(uuid.uuid4())
    first, second = instances("pod-a"), instances("pod-b")
    await first.execute(task_id)

    await write_task(first.provider, task_id, TaskState.TASK_STATE_WORKING)
    assert await task_state(task_id) == "TASK_STATE_WORKING"

    with pytest.raises(TaskOwnershipLost):
        await write_task(second.provider, task_id, TaskState.TASK_STATE_COMPLETED)
    assert await task_state(task_id) == "TASK_STATE_WORKING"


async def test_a_task_running_elsewhere_cannot_be_attached_to(instances) -> None:
    """A stream opened here would never produce an event."""
    task_id = str(uuid.uuid4())
    first, second = instances("pod-a"), instances("pod-b")
    await first.execute(task_id)
    await write_task(first.provider, task_id, TaskState.TASK_STATE_WORKING)

    with pytest.raises(TaskOwnershipBusy) as refused:
        await second.subscribe(task_id)

    assert refused.value.owner_instance_id == "pod-a"


async def test_cancelling_from_another_instance_stops_the_holder(instances) -> None:
    """The control request need not know which instance is executing.

    Cancellation marks the claim rather than writing a terminal state
    directly. The holder discovers the mark over ``TASK_EVENT_CHANNEL``
    (``CANCEL_REQUESTED``) - or, failing that, on its own next heartbeat
    renewal - cancels the execution locally through its ordinary A2A executor
    path, and writes CANCELED itself. The controller learns of it the same
    way, over ``CANCEL_RESOLVED``, not by polling, and returns that same
    terminal task to its own caller.
    """
    task_id = str(uuid.uuid4())
    holder = instances("pod-a", settings=short_lease())
    controller = instances("pod-b")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_WORKING)

    canceled = await controller.cancel(task_id)

    assert canceled is not None
    assert canceled.status.state == TaskState.TASK_STATE_CANCELED

    # CANCEL_RESOLVED fires inside save()'s own transaction, but the claim
    # row is only deleted afterward, in a separate call the holder makes once
    # save() returns (AionTaskManager._save_task) - so a controller woken by
    # the notification can observe the terminal task before that release
    # lands. Wait for it explicitly rather than asserting claim_count()
    # right away.
    await _until(lambda: holder.provider.claim_for(task_id) is None)
    assert await claim_count() == 0
    with pytest.raises(TaskOwnershipLost):
        await write_task(holder.provider, task_id, TaskState.TASK_STATE_COMPLETED)
    assert await task_state(task_id) == "TASK_STATE_CANCELED"


async def test_cancellation_reaches_the_owner_with_no_heartbeat_running(instances) -> None:
    """The push path is load-bearing on its own, not merely a heartbeat accelerant.

    The owner's heartbeat task is killed outright right after it takes the
    task on - reaching into ``_heartbeat_task`` directly, the same way this
    file already reaches into ``provider.claim_for``/``provider.renew``
    elsewhere, since there is no public "heartbeat off, push on" toggle and
    none is worth adding for one test. With the heartbeat gone, the only way
    the owner can still learn of a cancellation request is over
    ``TASK_EVENT_CHANNEL``. Complements
    ``test_cancel_wait_timeout_still_reports_current_state_and_converges_later``,
    which proves the opposite half: that polling alone still converges when
    push is disabled.
    """
    task_id = str(uuid.uuid4())
    holder = instances("pod-a")
    controller = instances("pod-b")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_WORKING)

    heartbeat = holder.provider._heartbeat_task
    heartbeat.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat
    holder.provider._heartbeat_task = None

    canceled = await controller.cancel(task_id)

    assert canceled is not None
    assert canceled.status.state == TaskState.TASK_STATE_CANCELED


async def test_cancelling_an_already_terminal_task_raises(instances) -> None:
    """A cancellation that arrives too late is an error, not a silent no-op.

    A successful cancellation is itself terminal, so the two cases must be
    told apart here rather than left for the caller to infer from state.
    """
    task_id = str(uuid.uuid4())
    holder = instances("pod-a")
    controller = instances("pod-b")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_COMPLETED)

    with pytest.raises(TaskNotCancelableError):
        await controller.cancel(task_id)


async def test_a_task_that_completes_before_the_owner_notices_the_signal_keeps_its_outcome(
    instances,
) -> None:
    """A legitimate outcome must not be overwritten by a request that lost the race.

    Deterministic version of the race, not a timing-dependent one: the mark
    is placed first, exactly as it would be mid-run, and the owner is then
    made to reach its own outcome before anything acts on that mark - the
    same as if the owner's heartbeat simply had not ticked yet, and, with
    ``enable_push=False``, as if ``TASK_EVENT_CHANNEL`` had not delivered the
    mark either. Push is disabled here specifically so this stays
    deterministic instead of a race against the listener's own round trip
    to Postgres and back. The mark must not retroactively change what already
    happened; the client's original request is answered as "too late" instead.
    """
    task_id = str(uuid.uuid4())
    holder = instances("pod-a", enable_push=False)
    controller = instances("pod-b")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_WORKING)

    assert await controller.store.request_cancellation(task_id) is True

    # The owner reaches its own outcome unaware of the mark - the same fenced
    # write it would make regardless, still using its still-current token.
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_COMPLETED)
    assert await task_state(task_id) == "TASK_STATE_COMPLETED"

    with pytest.raises(TaskNotCancelableError):
        await controller.cancel(task_id)
    assert await task_state(task_id) == "TASK_STATE_COMPLETED"


async def test_cancelling_a_task_with_no_live_claim_writes_canceled_directly(
    instances,
) -> None:
    """Branch three of ``on_cancel_task``: nobody to signal, so no waiting.

    An interrupted task's owner has already released its claim on purpose -
    there is no heartbeat left to deliver a signal to, so the only correct
    move is the direct, unconditional write the store already provides.
    """
    task_id = str(uuid.uuid4())
    holder = instances("pod-a")
    controller = instances("pod-b")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_INPUT_REQUIRED)
    async with _db_manager.get_session() as session:
        await session.execute(text("DELETE FROM task_claims"))
        await session.commit()

    canceled = await controller.cancel(task_id)

    assert canceled is not None
    assert canceled.status.state == TaskState.TASK_STATE_CANCELED
    assert await claim_count() == 0


async def test_cancel_wait_timeout_still_reports_current_state_and_converges_later(
    instances, monkeypatch
) -> None:
    """A wait that outruns its budget must not block the caller further.

    The holder's push subscription is disabled (``enable_push=False``), and
    its heartbeat interval is left at the deployed default - far longer than
    the wait budget this test shrinks it against - so the controller's wait
    is guaranteed to time out with neither delivery path having fired yet.
    The mark it left behind is not lost, though: the same task converges to
    CANCELED once the owner's own heartbeat eventually ticks - simulated
    here by driving one renewal directly rather than waiting the real
    interval out.
    """
    monkeypatch.setattr(_request_handler_module, "CANCEL_WAIT_SECONDS", 0.05)
    task_id = str(uuid.uuid4())
    holder = instances("pod-a", enable_push=False)
    controller = instances("pod-b")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_WORKING)

    reported = await controller.cancel(task_id)

    assert reported is not None
    assert reported.status.state == TaskState.TASK_STATE_WORKING
    assert await claim_count() == 1

    claim = holder.provider.claim_for(task_id)
    await holder.provider.renew(claim)

    await _until(lambda: holder.provider.claim_for(task_id) is None)
    assert await task_state(task_id) == "TASK_STATE_CANCELED"


async def test_concurrent_cancels_from_different_instances_agree(instances) -> None:
    """Two callers racing the same cancellation must not fight each other.

    Both requests mark the same claim - the second finds the mark already
    there and leaves it alone - and both wait on the very same local
    ``asyncio.Event``, so the one notification the owner eventually sends
    wakes both at once.
    """
    task_id = str(uuid.uuid4())
    holder = instances("pod-a", settings=short_lease())
    controller_a = instances("pod-b")
    controller_b = instances("pod-c")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_WORKING)

    first, second = await asyncio.gather(
        controller_a.cancel(task_id), controller_b.cancel(task_id)
    )

    assert first.status.state == TaskState.TASK_STATE_CANCELED
    assert second.status.state == TaskState.TASK_STATE_CANCELED

    # See test_cancelling_from_another_instance_stops_the_holder: the claim
    # release is a separate call the holder makes after save() returns, so a
    # controller woken by CANCEL_RESOLVED can observe the terminal task
    # before that release lands.
    await _until(lambda: holder.provider.claim_for(task_id) is None)
    assert await claim_count() == 0


async def test_a_task_left_by_a_dead_instance_is_settled(instances) -> None:
    """The whole point of an expiring lease, end to end.

    An instance stops without saying anything, so its lease is left to expire.
    While it is unexpired the task belongs to nobody reachable and no other
    instance may touch it. Once it expires, another instance reclaims it and
    gives the task an outcome.
    """
    task_id = str(uuid.uuid4())
    dead = instances("pod-a")
    survivor = instances("pod-b", reconciler_enabled=True)
    await dead.execute(task_id)
    await write_task(dead.provider, task_id, TaskState.TASK_STATE_WORKING)

    with pytest.raises(TaskOwnershipBusy):
        await survivor.subscribe(task_id)

    await expire_all()
    assert await survivor.provider.reconcile() == 1

    assert await task_state(task_id) == "TASK_STATE_FAILED"
    assert await claim_count() == 0


async def test_a_settled_task_is_replayed_to_a_subscriber(instances) -> None:
    """Reconnecting to a finished task is the most ordinary client behaviour.

    It is also the reason the reaper settles a task rather than deleting it:
    the outcome exists to be read. The instance replaying it is not the one
    that ran the task and never held its lease.
    """
    task_id = str(uuid.uuid4())
    dead = instances("pod-a")
    survivor = instances("pod-b", reconciler_enabled=True)
    await dead.execute(task_id)
    await write_task(dead.provider, task_id, TaskState.TASK_STATE_WORKING)
    await expire_all()
    await survivor.provider.reconcile()

    events = await survivor.subscribe(task_id)

    assert len(events) == 1
    assert isinstance(events[0], Task)
    assert events[0].id == task_id
    assert events[0].status.state == TaskState.TASK_STATE_FAILED


async def test_a_task_waiting_for_input_is_replayed_without_an_execution(
    instances,
) -> None:
    """The reconnect that happens most often, on the instance that gets it.

    A conversation waiting for the user has no owner: its lease was released
    on purpose. Any instance may answer the reconnect, and the answer is the
    stored task. What it must not leave behind is an execution - nothing here
    will ever finish it, and the resume acquires a lease and replaces it.
    """
    task_id = str(uuid.uuid4())
    first, second = instances("pod-a"), instances("pod-b")
    await first.execute(task_id)
    await write_task(first.provider, task_id, TaskState.TASK_STATE_INPUT_REQUIRED)

    events = await second.subscribe(task_id)

    assert len(events) == 1
    assert isinstance(events[0], Task)
    assert events[0].status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert second.registry._active_tasks == {}
    assert second.registry._task_managers == {}


async def test_shutdown_settles_what_it_interrupts_and_frees_the_task(
    instances,
) -> None:
    """An orderly stop is not the same failure as a killed process.

    The instance still holds its lease at that moment, so it writes the outcome
    itself and releases. Nothing is left for the reaper, and the next instance
    is refused nothing.
    """
    task_id = str(uuid.uuid4())
    first = instances("pod-a")
    await first.execute(task_id)
    await write_task(first.provider, task_id, TaskState.TASK_STATE_WORKING)

    await first.aclose()

    assert await task_state(task_id) == "TASK_STATE_FAILED"
    assert first.provider.claim_for(task_id) is None


async def _until(condition, timeout: float = 5.0) -> None:
    """Wait for a background result, failing the test if it never arrives.

    Polled rather than awaited: what is being waited for is the effect of a
    supervisor loop this test does not drive, and there is no event to attach
    to. The timeout is generous because the assertion is that it happens at
    all, not how quickly.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("The expected background effect did not happen in time")
