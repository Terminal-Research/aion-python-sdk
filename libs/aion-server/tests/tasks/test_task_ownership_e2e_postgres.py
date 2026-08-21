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

from a2a.types import CancelTaskRequest, SubscribeToTaskRequest, Task, TaskState

from aion.server.agent.execution.scope import init_execution_scope
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.ownership import (
    PostgresOwnershipProvider,
    TaskOwnershipBusy,
    TaskOwnershipLost,
)
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

STREAM_TIMEOUT_SECONDS = 10.0
"""How long a resubscribe may take before the test calls it a hang."""

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


def _instance(name: str, **provider_kwargs) -> _Instance:
    """Assemble one instance over the shared database.

    The store and the provider are the pair ``StoreManager`` selects together,
    and the handler is given that same provider, which is how a deployed
    process is built. Nothing here is a stub except the agent executor and the
    agent card: no test in this module runs agent logic, only the coordination
    around it.
    """
    provider = _provider(name, **provider_kwargs)
    store = PostgresTaskStore(agent_id=provider.agent_id, ownership_provider=provider)
    agent_card = Mock()
    agent_card.capabilities.streaming = True
    handler = AionRequestHandler(
        agent_executor=Mock(),
        task_store=store,
        agent_card=agent_card,
        ownership_provider=provider,
    )
    return _Instance(name=name, provider=provider, store=store, handler=handler)


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
    assert refused.value.data["retryable"] is True
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

    Cancellation removes the lease. The holder learns of it from its own
    heartbeat, gives up the execution, and can no longer write.
    """
    task_id = str(uuid.uuid4())
    holder = instances("pod-a", settings=short_lease())
    controller = instances("pod-b")
    await holder.execute(task_id)
    await write_task(holder.provider, task_id, TaskState.TASK_STATE_WORKING)

    canceled = await controller.cancel(task_id)

    assert canceled is not None
    assert canceled.status.state == TaskState.TASK_STATE_CANCELED
    assert await claim_count() == 0

    await _until(lambda: holder.provider.claim_for(task_id) is None)
    with pytest.raises(TaskOwnershipLost):
        await write_task(holder.provider, task_id, TaskState.TASK_STATE_COMPLETED)
    assert await task_state(task_id) == "TASK_STATE_CANCELED"


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
