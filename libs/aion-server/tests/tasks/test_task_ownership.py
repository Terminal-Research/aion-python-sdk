"""Focused tests for task ownership supervision and interrupt cleanup hooks."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from unittest.mock import AsyncMock, Mock

import pytest
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent

from aion.server.agent.execution.active_task_registry import AionActiveTaskRegistry
from aion.server.tasks.ownership import (
    RECONCILER_ENV_VAR,
    Busy,
    Claim,
    LeaseSettings,
    Lost,
    Owned,
    OwnershipHeartbeat,
    PostgresOwnershipProvider,
    Unknown,
)
from aion.server.tasks.task_manager import AionTaskManager


TASK_ID = str(uuid.uuid4())
CONTEXT_ID = "context-1"


def _claim_row(token: uuid.UUID) -> dict:
    """Build a fake ``task_claims`` row matching what TaskClaimsRepository returns."""
    now = datetime.now(timezone.utc)
    return {
        "task_id": uuid.UUID(TASK_ID),
        "owner_token": token,
        "lease_expires_at": now,
        "acquired_at": now,
        "renewed_at": now,
        "owner_instance_id": None,
    }


class _ScriptedProvider:
    """Small heartbeat port whose renewal outcomes are controlled by a test."""

    settings = LeaseSettings(unknown_retry_seconds=0.001)

    def __init__(
        self,
        outcomes: list[Owned | Lost | Unknown],
        delay: float = 0.0,
    ) -> None:
        """Store the scripted outcomes, the call delay, and loss notifications."""
        self.outcomes = deque(outcomes)
        self.delay = delay
        self.losses: list[tuple[str, str]] = []
        self.renew_calls = 0
        self.releases = 0

    async def renew(self, claim: Claim):
        """Return the next scripted renewal outcome after the scripted delay."""
        self.renew_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.outcomes.popleft()

    def mark_lost(self, claim: Claim, reason: str) -> None:
        """Record fail-closed notifications from the heartbeat."""
        self.losses.append((claim.task_id, reason))


def _claim(*, deadline: float | None = None) -> Claim:
    """Build a claim with a short deterministic task identifier."""
    return Claim(
        task_id=TASK_ID,
        owner_token=uuid.uuid4(),
        lease_expires_at=None,
        deadline=deadline if deadline is not None else time.monotonic() + 1,
    )


@pytest.mark.anyio
async def test_heartbeat_lost_does_not_release() -> None:
    """A definitive loss notifies the registry and never calls release."""
    provider = _ScriptedProvider([Lost()])

    await OwnershipHeartbeat(provider)._renew_until_known(_claim())

    assert provider.losses == [(TASK_ID, "renew_lost")]
    assert provider.releases == 0


@pytest.mark.anyio
async def test_heartbeat_retries_unknown_until_success() -> None:
    """A transient database failure is retried before the safe deadline."""
    provider = _ScriptedProvider([Unknown(RuntimeError("temporary")), Owned()])

    await OwnershipHeartbeat(provider)._renew_until_known(_claim())

    assert provider.renew_calls == 2
    assert provider.losses == []


@pytest.mark.anyio
async def test_heartbeat_fails_closed_at_unknown_deadline() -> None:
    """Repeated unknown renewals stop at the local monotonic deadline."""
    provider = _ScriptedProvider([Unknown(RuntimeError("database unavailable"))] * 50)
    claim = _claim(deadline=time.monotonic() + 0.01)

    await asyncio.wait_for(
        OwnershipHeartbeat(provider)._renew_until_known(claim),
        timeout=0.2,
    )

    assert provider.losses == [(TASK_ID, "renew_deadline")]


class _Result:
    """Minimal SQLAlchemy result facade for a no-row renewal."""

    def mappings(self):
        """Return this result as a mapping result."""
        return self

    def first(self):
        """Represent an UPDATE ... RETURNING with no affected row."""
        return None


class _MappingResult(_Result):
    """Result facade that can return one mapping row."""

    def __init__(self, row=None) -> None:
        """Store the optional returned row."""
        self.row = row

    def first(self):
        """Return the configured mapping row."""
        return self.row


class _Session:
    """Minimal async session context for provider unit tests."""

    async def __aenter__(self):
        """Enter the fake session."""
        return self

    async def __aexit__(self, *_):
        """Leave the fake session."""

    async def execute(self, *_args, **_kwargs):
        """Return a no-row SQL result."""
        return _Result()

    async def commit(self):
        """Commit the fake transaction."""


class _DbManager:
    """Database manager returning one fake async session."""

    def get_session(self):
        """Return the fake session context manager."""
        return _Session()


class _AcquireSession:
    """Session returning one claim row after the task lock statement."""

    def __init__(self, row=None) -> None:
        """Store the claim row and count SQL statements."""
        self.row = row
        self.execute_calls = 0

    async def __aenter__(self):
        """Enter the fake session."""
        return self

    async def __aexit__(self, *_):
        """Leave the fake session."""

    async def execute(self, *_args, **_kwargs):
        """Return an empty task-lock result, then the configured claim."""
        self.execute_calls += 1
        return _MappingResult(self.row if self.execute_calls == 2 else None)

    async def commit(self):
        """Commit the fake transaction."""


class _AcquireDbManager:
    """Database manager exposing one inspectable acquire session."""

    def __init__(self, row=None) -> None:
        """Create the session with a configured claim row."""
        self.session = _AcquireSession(row)

    def get_session(self):
        """Return the inspectable acquire session."""
        return self.session


@pytest.mark.anyio
async def test_acquire_reuses_local_claim_without_second_database_acquire() -> None:
    """A repeated execute request presents the same token receipt."""
    token = uuid.uuid4()
    db_manager = _AcquireDbManager(_claim_row(token))
    provider = PostgresOwnershipProvider(
        db_manager=db_manager,
        task_id_parser=lambda _task_id: uuid.UUID(TASK_ID),
    )

    first = await provider.acquire(TASK_ID)
    second = await provider.acquire(TASK_ID)

    assert first is second
    assert isinstance(first, Claim)
    assert first.owner_token == token
    assert db_manager.session.execute_calls == 2


@pytest.mark.anyio
async def test_postgres_renew_lost_removes_receipt_and_notifies() -> None:
    """A zero-row fenced renewal removes the exact local incarnation."""
    provider = PostgresOwnershipProvider(
        db_manager=_DbManager(),
        task_id_parser=lambda _task_id: uuid.UUID(TASK_ID),
    )
    claim = _claim()
    provider._claims[TASK_ID] = claim
    losses: list[tuple[str, str]] = []
    provider.set_loss_callback(lambda task_id, reason: losses.append((task_id, reason)))

    result = await provider.renew(claim)

    assert isinstance(result, Lost)
    assert provider.claim_for(TASK_ID) is None
    assert losses == [(TASK_ID, "renew_lost")]


class _TaskStore:
    """Minimal store for testing post-save interrupt notification."""

    def __init__(self) -> None:
        """Create a working task."""
        self.task = Task(
            id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )

    async def get(self, task_id: str, _context=None):
        """Return the current task for its canonical id."""
        return self.task if task_id == TASK_ID else None

    async def save(self, task: Task, _context=None):
        """Persist the task in memory."""
        self.task = task


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED],
)
async def test_task_manager_notifies_after_interrupt_is_persisted(state: TaskState) -> None:
    """Both interrupt states invoke cleanup only after the store save."""
    store = _TaskStore()
    callback = Mock()
    manager = AionTaskManager(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        task_store=store,
        context=Mock(),
        initial_message=None,
        on_interrupted=callback,
    )

    await manager.save_task_event(
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=state),
        )
    )

    assert store.task.status.state == state
    callback.assert_called_once_with(TASK_ID)


@pytest.mark.anyio
async def test_old_cleanup_cannot_remove_replacement() -> None:
    """Deferred cleanup is identity-aware when a task id is reused."""
    registry = AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=Mock(),
        push_sender=None,
    )
    old = Mock(task_id=TASK_ID, _task_manager=Mock())
    replacement = Mock(task_id=TASK_ID, _task_manager=Mock(), aclose=AsyncMock())
    registry._active_tasks[TASK_ID] = replacement
    registry._task_managers[TASK_ID] = replacement._task_manager

    await registry._remove_task_for_incarnation(old)

    assert registry._active_tasks[TASK_ID] is replacement
    assert registry._task_managers[TASK_ID] is replacement._task_manager
    await registry.aclose()


class _HangingSession:
    """Session whose statement never answers, standing in for a wedged link."""

    async def __aenter__(self):
        """Enter the fake session."""
        return self

    async def __aexit__(self, *_):
        """Leave the fake session."""

    async def execute(self, *_args, **_kwargs):
        """Never return, the way a hung connection never returns."""
        await asyncio.Event().wait()

    async def commit(self):
        """Unreachable."""


class _HangingDbManager:
    """Database manager handing out a session that never answers."""

    def get_session(self):
        """Return the hanging session."""
        return _HangingSession()


@pytest.mark.anyio
async def test_renew_reports_unknown_when_the_statement_never_answers() -> None:
    """A wedged connection must not park the supervisor indefinitely.

    The fail-closed deadline is only evaluated between attempts, so a renewal
    that neither succeeds nor raises would hold every lease in the process past
    its expiry while another instance legitimately takes the work over.
    """
    provider = PostgresOwnershipProvider(
        db_manager=_HangingDbManager(),
        task_id_parser=lambda _task_id: uuid.UUID(TASK_ID),
        settings=LeaseSettings(statement_timeout_seconds=0.05),
    )

    result = await asyncio.wait_for(provider.renew(_claim()), timeout=1)

    assert isinstance(result, Unknown)
    assert isinstance(result.error, TimeoutError)


@pytest.mark.anyio
async def test_concurrent_acquires_reach_the_database_once() -> None:
    """Two requests for one task must not make the process busy against itself.

    The second upsert would meet the first one's live lease and be refused, so
    a plain race would report the task owned elsewhere when this process is the
    owner.
    """
    token = uuid.uuid4()
    db_manager = _AcquireDbManager(_claim_row(token))
    provider = PostgresOwnershipProvider(
        db_manager=db_manager,
        task_id_parser=lambda _task_id: uuid.UUID(TASK_ID),
    )

    first, second = await asyncio.gather(
        provider.acquire(TASK_ID),
        provider.acquire(TASK_ID),
    )

    assert first is second
    assert isinstance(first, Claim)
    assert db_manager.session.execute_calls == 2


@pytest.mark.anyio
async def test_confirmed_renewal_past_the_old_deadline_keeps_the_claim() -> None:
    """A renewal that returned proves ownership even if it answered late.

    The deadline exists to stop work that cannot prove ownership. A renewal
    still in flight when it passes has since proven exactly that, and tearing
    the execution down anyway would kill a task holding a valid lease.
    """
    provider = _ScriptedProvider([Owned()], delay=0.05)
    claim = _claim(deadline=time.monotonic() + 0.02)

    await OwnershipHeartbeat(provider)._renew_until_known(claim)

    assert provider.losses == []


def test_reaper_is_on_unless_its_switch_is_cleared(monkeypatch) -> None:
    """The reaper defaults on, assuming the heartbeat is already everywhere."""
    monkeypatch.delenv(RECONCILER_ENV_VAR, raising=False)
    assert PostgresOwnershipProvider(db_manager=_DbManager()).reconciler_enabled is True

    monkeypatch.setenv(RECONCILER_ENV_VAR, "false")
    assert PostgresOwnershipProvider(db_manager=_DbManager()).reconciler_enabled is False


def test_owner_instance_id_prefers_explicit_value(monkeypatch) -> None:
    """An injected runtime identity takes precedence over the environment."""
    monkeypatch.setenv("HOST_NAME", "pod-from-environment")

    provider = PostgresOwnershipProvider(
        db_manager=_DbManager(),
        owner_instance_id="pod-from-caller",
    )

    assert provider.owner_instance_id == "pod-from-caller"


def test_owner_instance_id_uses_host_name(monkeypatch) -> None:
    """The deployment-provided host name identifies the claim holder."""
    monkeypatch.setenv("HOST_NAME", "pod-from-environment")

    provider = PostgresOwnershipProvider(db_manager=_DbManager())

    assert provider.owner_instance_id == "pod-from-environment"


def test_blank_owner_instance_id_falls_back_to_host_name(monkeypatch) -> None:
    """An empty injected value is absent rather than an instance identity."""
    monkeypatch.setenv("HOST_NAME", "pod-from-environment")

    provider = PostgresOwnershipProvider(
        db_manager=_DbManager(),
        owner_instance_id="",
    )

    assert provider.owner_instance_id == "pod-from-environment"


def test_owner_instance_id_is_none_without_host_name(monkeypatch) -> None:
    """Legacy host variables do not silently become the instance identity."""
    monkeypatch.delenv("HOST_NAME", raising=False)
    monkeypatch.setenv("POD_NAME", "legacy-pod")
    monkeypatch.setenv("HOSTNAME", "legacy-host")

    provider = PostgresOwnershipProvider(db_manager=_DbManager())

    assert provider.owner_instance_id is None


@pytest.mark.anyio
async def test_disabled_reaper_touches_nothing() -> None:
    """A disabled reaper reports no work rather than querying for candidates."""
    provider = PostgresOwnershipProvider(
        db_manager=_DbManager(),
        reconciler_enabled=False,
    )

    assert await provider.reconcile() == 0


class _RefusingDbManager:
    """Database manager whose every acquire is refused."""

    def get_session(self):
        """Return a session that answers no claim row."""
        return _AcquireSession(row=None)


class _FailingSession:
    """Session that fails the moment it is entered."""

    async def __aenter__(self):
        """Fail on entry, the way an unreachable database does."""
        raise ConnectionError("database is unreachable")

    async def __aexit__(self, *_):
        """Never reached."""


class _FailingDbManager:
    """Database manager that cannot open a session."""

    def get_session(self):
        """Return the failing session context manager."""
        return _FailingSession()


def _refusing_provider(db_manager) -> PostgresOwnershipProvider:
    """Build a provider that parses every task id as a fresh UUID."""
    return PostgresOwnershipProvider(
        db_manager=db_manager,
        task_id_parser=lambda task_id: uuid.UUID(task_id),
    )


@pytest.mark.anyio
async def test_refused_acquires_leave_no_locks_behind() -> None:
    """A per-task lock lives for the length of an attempt, not of a lease.

    A refusal hands back no claim, so nothing is released later and nothing
    would ever remove the lock. A process that mostly meets tasks owned by
    other instances refuses most of its acquires, which is exactly where an
    entry per task id must not accumulate.
    """
    provider = _refusing_provider(_RefusingDbManager())

    for _ in range(1000):
        assert isinstance(await provider.acquire(str(uuid.uuid4())), Busy)

    assert provider._claims == {}
    assert provider._acquire_locks == {}
    assert provider._acquire_lock_users == {}


@pytest.mark.anyio
async def test_a_failed_acquire_leaves_no_lock_behind() -> None:
    """An unreachable database is the other outcome with nothing to release."""
    provider = _refusing_provider(_FailingDbManager())

    with pytest.raises(ConnectionError):
        await provider.acquire(str(uuid.uuid4()))

    assert provider._acquire_locks == {}
    assert provider._acquire_lock_users == {}


@pytest.mark.anyio
async def test_a_released_claim_leaves_no_lock_behind() -> None:
    """The ordinary path drops the lock when its acquire ends, not later."""
    token = uuid.uuid4()
    provider = PostgresOwnershipProvider(
        db_manager=_AcquireDbManager(_claim_row(token)),
        task_id_parser=lambda _task_id: uuid.UUID(TASK_ID),
    )

    claim = await provider.acquire(TASK_ID)
    assert isinstance(claim, Claim)
    assert provider._acquire_locks == {}

    await provider.release(claim)
    assert provider._claims == {}
    assert provider._acquire_locks == {}
    assert provider._acquire_lock_users == {}
