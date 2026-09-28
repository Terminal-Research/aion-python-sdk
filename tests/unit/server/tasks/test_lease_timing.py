"""Lease timing: the scaled settings, and the heartbeat's deadline under a hung database.

The heartbeat tests run on a virtual clock. What they check is arithmetic about
the worst case - a renewal that hangs for its whole statement timeout, a retry
after the longest backoff - and on a real clock that worst case either takes
seconds per TTL or has to be scaled down until scheduling noise is the same
size as the reserve being measured. The heartbeat's ``time``, ``random`` and
``asyncio`` are replaced for the duration of each test, so every sleep and
every hang moves the clock by exactly its nominal length.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from aion.server.settings import AppSettings
from aion.server.tasks.ownership import (
    Claim,
    LeaseSettings,
    OwnershipHeartbeat,
    Owned,
    PostgresOwnershipProvider,
    Unknown,
)
from aion.server.tasks.ownership import heartbeat as heartbeat_module
from aion.server.tasks.ownership.config import (
    CANCEL_GRACE_SECONDS,
    CANCEL_WAIT_SECONDS,
    LEASE_TTL_SECONDS,
    MIN_LEASE_TTL_SECONDS,
    MIN_RENEWAL_RESERVE_SECONDS,
    lease_settings_from_environment,
)
from aion.server.agent.execution.extensions.evolution.handler import _CANCEL_DRAIN_TIMEOUT_S

SCENARIO_LEASE_TTL_SECONDS = 24.0
"""The TTL ``tests/scenarios`` run their servers at; kept equal by a test below."""

TTLS = tuple(sorted({MIN_LEASE_TTL_SECONDS, SCENARIO_LEASE_TTL_SECONDS, LEASE_TTL_SECONDS}))


class _Clock:
    """A monotonic clock that only moves when the test says so."""

    def __init__(self) -> None:
        """Start at zero: the moment of the last confirmed renewal."""
        self.now = 0.0

    def monotonic(self) -> float:
        """Report virtual time."""
        return self.now


class _HungDatabase:
    """A provider whose renewals hang until their time bound runs out.

    It applies ``timeout_seconds`` exactly as ``PostgresOwnershipProvider``
    does - narrowing its statement timeout, and reporting the expiry as
    ``Unknown`` - and records every attempt, so a test can read back when each
    one started and how long it was allowed to run.
    """

    def __init__(self, settings: LeaseSettings, clock: _Clock, *, hangs: int) -> None:
        """Hang the first ``hangs`` attempts; answer the rest at once."""
        self.settings = settings
        self.clock = clock
        self.hangs = hangs
        self.attempts: list[SimpleNamespace] = []
        self.losses: list[tuple[str, str, float]] = []

    async def renew_batch(self, claims: list[Claim], *, timeout_seconds: float | None = None):
        """Hang for the whole bound, or renew every claim in the batch."""
        limit = self.settings.statement_timeout_seconds
        if timeout_seconds is not None:
            limit = max(0.0, min(limit, timeout_seconds))
        self.attempts.append(
            SimpleNamespace(
                start=self.clock.now,
                limit=limit,
                task_ids=[claim.task_id for claim in claims],
            )
        )
        if len(self.attempts) <= self.hangs:
            self.clock.now += limit
            return Unknown(TimeoutError())
        return {claim.task_id: Owned() for claim in claims}

    def mark_lost(self, claim: Claim, reason: str) -> None:
        """Record when and why the heartbeat gave a claim up."""
        self.losses.append((claim.task_id, reason, self.clock.now))


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    """Put the heartbeat on a virtual clock, with every backoff at its longest."""
    clock = _Clock()
    real_sleep = asyncio.sleep

    async def sleep(seconds: float) -> None:
        clock.now += seconds
        await real_sleep(0)

    monkeypatch.setattr(heartbeat_module, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(heartbeat_module, "random", SimpleNamespace(uniform=lambda low, high: high))
    monkeypatch.setattr(
        heartbeat_module,
        "asyncio",
        SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
    )
    return clock


def _claim(task_id: str, deadline: float) -> Claim:
    """A claim whose fail-closed deadline is ``deadline`` on the virtual clock."""
    return Claim(
        task_id=task_id,
        owner_token=uuid.uuid4(),
        lease_expires_at=None,
        deadline=deadline,
    )


@pytest.mark.parametrize("ttl", TTLS)
async def test_a_retry_after_a_hung_renewal_runs_in_full_before_the_deadline(
    clock: _Clock, ttl: float
) -> None:
    """The one failure the retry exists for still leaves room for it.

    The tick comes one heartbeat interval after the last confirmed renewal,
    that renewal hangs for its whole statement timeout, and the retry waits
    the longest backoff. The retry must then get its whole statement timeout
    too - not a remainder cut short by the deadline - and still end with a
    reserve to spare.
    """
    settings = LeaseSettings.for_ttl(ttl)
    database = _HungDatabase(settings, clock, hangs=1)
    claim = _claim("task", deadline=settings.working_window_seconds)
    clock.now = settings.heartbeat_interval_seconds

    await OwnershipHeartbeat(database)._renew_all([claim])

    assert database.losses == []
    first, retry = database.attempts
    assert first.limit == settings.statement_timeout_seconds
    assert retry.limit == settings.statement_timeout_seconds
    assert retry.start + retry.limit <= claim.deadline - MIN_RENEWAL_RESERVE_SECONDS


@pytest.mark.parametrize("ttl", TTLS)
async def test_no_renewal_runs_past_the_deadline_when_the_database_never_answers(
    clock: _Clock, ttl: float
) -> None:
    """Every attempt ends by the deadline, and the claim is given up at it.

    The attempt after the last backoff is the one that used to escape: it
    started with the deadline already near and ran for a whole statement
    timeout past it.
    """
    settings = LeaseSettings.for_ttl(ttl)
    database = _HungDatabase(settings, clock, hangs=1_000)
    claim = _claim("task", deadline=settings.working_window_seconds)
    clock.now = settings.heartbeat_interval_seconds

    await OwnershipHeartbeat(database)._renew_all([claim])

    assert len(database.attempts) >= 2
    for attempt in database.attempts:
        assert attempt.start < claim.deadline
        assert attempt.start + attempt.limit <= claim.deadline
    assert database.losses == [("task", "renew_deadline", claim.deadline)]


async def test_a_batch_cut_short_by_one_deadline_gives_up_only_that_claim(
    clock: _Clock,
) -> None:
    """Running out of the nearest deadline is uncertainty, not a loss for everyone.

    The attempt is bounded by the claim whose deadline comes first. When that
    bound runs out, the batch outcome is ``Unknown``: the claim at its
    deadline is given up as ``renew_deadline``, and the other one, still
    well inside its window, is retried on its own and renewed.
    """
    settings = LeaseSettings.for_ttl(SCENARIO_LEASE_TTL_SECONDS)
    database = _HungDatabase(settings, clock, hangs=1)
    closing = _claim("closing", deadline=1.0)
    healthy = _claim("healthy", deadline=settings.working_window_seconds)

    await OwnershipHeartbeat(database)._renew_all([closing, healthy])

    first, retry = database.attempts
    assert first.task_ids == ["closing", "healthy"]
    assert first.limit == 1.0
    assert retry.task_ids == ["healthy"]
    assert database.losses == [("closing", "renew_deadline", 1.0)]


class _HangingSession:
    """A session whose statement never returns."""

    async def __aenter__(self):
        """Enter the fake session."""
        return self

    async def __aexit__(self, *_):
        """Leave the fake session."""

    async def execute(self, *_args, **_kwargs):
        """Hang until cancelled."""
        await asyncio.Event().wait()

    async def commit(self):
        """Never reached."""


async def test_the_provider_reports_a_call_bound_that_ran_out_as_unknown() -> None:
    """The narrower bound is applied where the statement timeout is.

    Inside the provider, running out of it lands in the same ``except`` as
    any other failed statement and comes back as ``Unknown`` - not as a
    ``TimeoutError`` escaping into the heartbeat, whose last-resort handler
    would give up every claim in the batch.
    """
    provider = PostgresOwnershipProvider(
        "test-agent",
        db_manager=SimpleNamespace(get_session=_HangingSession),
        task_id_parser=lambda task_id: uuid.UUID(task_id),
        settings=LeaseSettings.for_ttl(LEASE_TTL_SECONDS),
    )
    claim = _claim(str(uuid.uuid4()), deadline=0.0)

    outcome = await asyncio.wait_for(
        provider.renew_batch([claim], timeout_seconds=0.05), timeout=1.0
    )

    assert isinstance(outcome, Unknown)


def test_the_default_ttl_reproduces_the_deployed_timing() -> None:
    """Scaling is an identity at the default: production does not move."""
    assert LeaseSettings.for_ttl(LEASE_TTL_SECONDS) == LeaseSettings()


@pytest.mark.parametrize("ttl", (*TTLS, 600.0))
def test_every_allowed_ttl_keeps_the_timing_invariants(ttl: float) -> None:
    """What makes a TTL safe to configure, checked at the edges and beyond."""
    settings = LeaseSettings.for_ttl(ttl)

    assert settings.working_window_seconds > 0
    assert settings.heartbeat_interval_seconds <= settings.safety_margin_seconds
    assert settings.statement_timeout_seconds < settings.safety_margin_seconds
    assert settings.renewal_reserve_seconds >= MIN_RENEWAL_RESERVE_SECONDS
    assert settings.cancel_grace_seconds == CANCEL_GRACE_SECONDS
    assert settings.cancel_wait_seconds == CANCEL_WAIT_SECONDS


def test_the_cancel_grace_outlasts_the_evolution_drain() -> None:
    """The reaper must never force-close an evolution still in its rescue."""
    assert CANCEL_GRACE_SECONDS > _CANCEL_DRAIN_TIMEOUT_S


def test_a_ttl_below_the_minimum_is_refused() -> None:
    """Below the minimum the retry no longer fits; it is an error, not a clamp."""
    with pytest.raises(ValueError):
        LeaseSettings.for_ttl(MIN_LEASE_TTL_SECONDS - 1)


def test_the_setting_is_read_and_bounded_like_the_timing() -> None:
    """The variable is read, and its lower bound is the timing's minimum."""
    with mock.patch.dict(os.environ, {"TASK_OWNERSHIP_LEASE_TTL_SECONDS": "30"}):
        assert AppSettings().task_ownership_lease_ttl_seconds == 30.0

    field = AppSettings.model_fields["task_ownership_lease_ttl_seconds"]
    lower_bounds = [meta.ge for meta in field.metadata if hasattr(meta, "ge")]
    assert lower_bounds == [MIN_LEASE_TTL_SECONDS]
    assert field.default == LEASE_TTL_SECONDS

    with mock.patch.dict(os.environ, {"TASK_OWNERSHIP_LEASE_TTL_SECONDS": "10"}):
        with pytest.raises(ValueError):
            AppSettings()


def test_a_provider_without_settings_takes_the_configured_ttl() -> None:
    """The setting reaches the provider that ``StoreManager`` builds."""
    with mock.patch(
        "aion.server.settings.app_settings.task_ownership_lease_ttl_seconds", 30.0
    ):
        provider = PostgresOwnershipProvider(
            "test-agent", task_id_parser=lambda task_id: uuid.UUID(task_id)
        )
        assert lease_settings_from_environment() == LeaseSettings.for_ttl(30.0)

    assert provider.settings == LeaseSettings.for_ttl(30.0)


def test_the_scenario_ttl_is_the_one_the_scenario_servers_get() -> None:
    """The heartbeat tests above check the TTL the scenarios actually use."""
    from tests.scenarios.harness.pg import SCENARIO_LEASE_TTL_SECONDS as used

    assert used == SCENARIO_LEASE_TTL_SECONDS
