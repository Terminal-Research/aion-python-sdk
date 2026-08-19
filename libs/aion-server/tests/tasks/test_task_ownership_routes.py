"""Tests for the three intents that reach an owned task and their refusals.

Execute acquires a lease and is refused when another instance holds it.
Attach never acquires: it may join what this process runs, or replay a task
nothing is running, and is refused only when a live owner exists elsewhere.
Control never enters the registry at all.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from a2a.types import Task, TaskState, TaskStatus
from a2a.utils.errors import TaskNotCancelableError, TaskNotFoundError

from aion.server.agent.execution.active_task_registry import AionActiveTaskRegistry
from aion.server.tasks.ownership import Busy, Claim, TaskOwnershipBusy
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

TASK_ID = str(uuid.uuid4())


class _StubProvider:
    """Enforcing provider whose acquire outcome is chosen by the test."""

    enforcement_enabled = True
    reconciler_enabled = False

    def __init__(
        self,
        *,
        busy: bool = False,
        held: bool = False,
        owner_instance_id: str | None = "pod-owner",
    ) -> None:
        """Configure whether acquire is refused and whether a claim is held."""
        self.busy = busy
        self.owner_instance_id = owner_instance_id
        self.acquire_calls = 0
        self.released: list[Claim] = []
        self._claims: dict[str, Claim] = {}
        if held:
            self._claims[TASK_ID] = self._make_claim()

    @staticmethod
    def _make_claim() -> Claim:
        """Build a claim for the task under test."""
        return Claim(
            task_id=TASK_ID,
            owner_token=uuid.uuid4(),
            lease_expires_at=None,
            deadline=float("inf"),
        )

    async def acquire(self, task_id: str):
        """Return a claim or refuse, as configured."""
        self.acquire_calls += 1
        if self.busy:
            return Busy()
        claim = self._claims.setdefault(task_id, self._make_claim())
        return claim

    async def release(self, claim: Claim) -> None:
        """Record the release and drop the claim."""
        self.released.append(claim)
        self._claims.pop(claim.task_id, None)

    def claim_for(self, task_id: str) -> Claim | None:
        """Return the held claim, if any."""
        return self._claims.get(task_id)

    def snapshot(self) -> list[Claim]:
        """Return the held claims."""
        return list(self._claims.values())

    def set_loss_callback(self, callback) -> None:
        """Accept the registry callback."""

    def mark_lost(self, claim: Claim, reason: str) -> None:
        """Drop the claim."""
        self._claims.pop(claim.task_id, None)

    def start(self) -> None:
        """No supervision in the stub."""

    async def stop(self) -> None:
        """No supervision to stop."""

    async def reconcile(self) -> int:
        """No reconciliation in the stub."""
        return 0

    async def current_owner(self, task_id: str) -> str | None:
        """Return the configured owner name, for the Busy-enrichment tests."""
        return self.owner_instance_id


def _memory_store() -> InMemoryTaskStore:
    """An in-memory store with a fixed owner, so no call context is needed."""
    return InMemoryTaskStore(owner_resolver=lambda _context: "owner")


def _task(state: TaskState) -> Task:
    """Build a durable task snapshot in the given state."""
    return Task(id=TASK_ID, context_id="ctx", status=TaskStatus(state=state))


def _registry(provider, store) -> AionActiveTaskRegistry:
    """Build a registry over the given provider and store."""
    return AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=store,
        push_sender=None,
        ownership_provider=provider,
    )


@pytest.mark.anyio
async def test_execute_on_a_foreign_task_is_refused_before_anything_is_built() -> None:
    """A refusal after the runtime exists means a foreign task is already running.

    The error names the holding instance: nothing upstream of this process can
    otherwise tell which of the identical replicas to point a client at.
    """
    provider = _StubProvider(busy=True, owner_instance_id="pod-7")
    registry = _registry(provider, AsyncMock())

    with pytest.raises(TaskOwnershipBusy) as excinfo:
        await registry.get_or_create(
            TASK_ID,
            call_context=Mock(),
            create_task_if_missing=True,
        )

    assert excinfo.value.owner_instance_id == "pod-7"
    assert excinfo.value.data["owner_instance_id"] == "pod-7"
    assert registry._active_tasks == {}
    assert registry._task_managers == {}


@pytest.mark.anyio
async def test_attach_to_a_task_running_elsewhere_is_refused() -> None:
    """An active task with no local execution belongs to another instance."""
    store = AsyncMock()
    store.get.return_value = _task(TaskState.TASK_STATE_WORKING)
    registry = _registry(_StubProvider(owner_instance_id="pod-9"), store)

    with pytest.raises(TaskOwnershipBusy) as excinfo:
        await registry.get_for_attach(TASK_ID, Mock())

    assert excinfo.value.owner_instance_id == "pod-9"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    ],
)
async def test_attach_to_a_task_waiting_for_input_builds_no_execution(
    state: TaskState,
) -> None:
    """A task awaiting input is replayed, not joined.

    Refusing the attach outright would break the most ordinary client
    behaviour there is - reconnecting to a conversation that is waiting for an
    answer - so the request is allowed and answered from the store. What it
    must not do is build an execution: nothing here will ever finish it, and
    the resume will replace it rather than reuse it, so the object would sit
    in the registry until the process stops.
    """
    store = AsyncMock()
    store.get.return_value = _task(state)
    provider = _StubProvider()
    registry = _registry(provider, store)
    registry.get_or_create = AsyncMock()

    assert await registry.get_for_attach(TASK_ID, Mock()) is None
    registry.get_or_create.assert_not_called()
    assert provider.acquire_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    ],
)
async def test_a_single_process_still_attaches_to_a_task_waiting_for_input(
    state: TaskState,
) -> None:
    """Without enforcement the resume reuses this object, so it is worth having.

    One process, one registry: the answer arrives here or nowhere, the same
    ``ActiveTask`` carries it, and a subscriber attached now sees the next
    turn.
    """
    store = AsyncMock()
    store.get.return_value = _task(state)
    provider = _StubProvider()
    provider.enforcement_enabled = False
    registry = _registry(provider, store)
    expected = object()
    registry.get_or_create = AsyncMock(return_value=expected)

    assert await registry.get_for_attach(TASK_ID, Mock()) is expected
    assert provider.acquire_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    ],
)
async def test_attach_to_a_settled_task_builds_no_execution(state: TaskState) -> None:
    """A finished task is replayed from the store, not from an execution.

    ``ActiveTask.start`` refuses a terminal task, so creating one here would
    answer a client reading a finished turn with an invalid-parameters error.
    """
    store = AsyncMock()
    store.get.return_value = _task(state)
    provider = _StubProvider()
    registry = _registry(provider, store)
    registry.get_or_create = AsyncMock()

    assert await registry.get_for_attach(TASK_ID, Mock()) is None
    registry.get_or_create.assert_not_called()
    assert provider.acquire_calls == 0


@pytest.mark.anyio
async def test_attach_to_a_missing_task_is_not_found_rather_than_busy() -> None:
    """An absent task is a not-found answer, not an ownership conflict."""
    store = AsyncMock()
    store.get.return_value = None
    registry = _registry(_StubProvider(), store)

    with pytest.raises(TaskNotFoundError):
        await registry.get_for_attach(TASK_ID, Mock())


@pytest.mark.anyio
async def test_attach_joins_the_local_execution() -> None:
    """A subscriber attaches to what this process is already running."""
    provider = _StubProvider(held=True)
    registry = _registry(provider, AsyncMock())
    running = MagicMock(task_id=TASK_ID, _is_finished=asyncio.Event())
    registry._active_tasks[TASK_ID] = running

    assert await registry.get_for_attach(TASK_ID, Mock()) is running


@pytest.mark.anyio
async def test_cancelling_a_finished_task_is_reported_as_such() -> None:
    """The store reports the already-terminal case; it cannot be inferred later.

    A successful cancellation also ends in a terminal state, so a caller
    reading the returned state cannot tell the two apart.
    """
    store = _memory_store()
    await store.save(_task(TaskState.TASK_STATE_COMPLETED))

    with pytest.raises(TaskNotCancelableError):
        await store.cancel(TASK_ID)


@pytest.mark.anyio
async def test_cancelling_a_running_task_returns_the_canceled_task() -> None:
    """The ordinary path returns the task rather than raising."""
    store = _memory_store()
    await store.save(_task(TaskState.TASK_STATE_WORKING))

    task = await store.cancel(TASK_ID)

    assert task is not None
    assert task.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.anyio
async def test_cancelling_an_unknown_task_returns_nothing() -> None:
    """A missing task is distinguishable from one that cannot be canceled."""
    assert await _memory_store().cancel(TASK_ID) is None
