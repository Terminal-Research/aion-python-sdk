"""Settling abandoned tasks over the in-memory store: what is abandoned, what the settlement writes, the sweep.

A task is aged by moving its last write back in time rather than by waiting:
the store keeps the time of every task's last write, and that is what the
timeout is compared with.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from aion.core.a2a.enums import A2AMetadataKey
from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1
from aion.server.settings import AppSettings
from aion.server.tasks.abandoned import ABANDONED_MESSAGE, AbandonedTasks, InMemoryStaleTasks
from aion.server.tasks.stores import InMemoryTaskStore

TIMEOUT = 60.0
URI = "https://example.invalid/extensions/scripted/1.0.0"
SETTLED_REASON = A2AMetadataKey.SETTLED_REASON.value


def task(task_id: str, state: TaskState = TaskState.TASK_STATE_WORKING, *, routed: str | None = None) -> Task:
    built = Task(
        id=task_id,
        context_id="ctx-1",
        status=TaskStatus(
            state=state,
            message=Message(message_id=f"{task_id}-said", role=Role.ROLE_AGENT, parts=[Part(text="thinking")]),
        ),
    )
    if routed is not None:
        built.metadata[A2AMetadataKey.ROUTED_EXTENSION.value] = routed
    return built


async def stored(store: InMemoryTaskStore, *tasks: Task, idle: float = 0.0) -> None:
    """Save the tasks, written ``idle`` seconds ago."""
    for item in tasks:
        await store.save(item, ServerCallContext())
        entry = store.tasks[item.id]
        store.tasks[item.id] = entry._replace(updated_at=entry.updated_at - _dt.timedelta(seconds=idle))


def settler(store: InMemoryTaskStore, *, timeout: float = TIMEOUT, push_sender=None) -> AbandonedTasks:
    return AbandonedTasks(InMemoryStaleTasks(store), timeout=timeout, push_sender=push_sender)


async def not_here(_task_id: str) -> bool:
    return False


async def test_a_running_task_silent_past_the_timeout_is_settled_failed_with_the_reason() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1"), idle=TIMEOUT + 1)

    settled = await settler(store).settle("t-1")

    assert settled is not None and settled.status.state == TaskState.TASK_STATE_FAILED
    kept = await store.get("t-1")
    assert kept.status.state == TaskState.TASK_STATE_FAILED
    assert kept.metadata[SETTLED_REASON] == "task_abandoned"
    assert kept.status.message.parts[0].text == "thinking", "a framework task keeps what it last said"
    assert [m.message_id for m in kept.history] == ["t-1-said"], "and records it as its final message"


async def test_an_extension_task_says_why_in_the_shape_of_an_extension_failure() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1", TaskState.TASK_STATE_SUBMITTED, routed=URI), idle=TIMEOUT + 1)

    settled = await settler(store).settle("t-1")

    text, data = settled.status.message.parts
    assert text.text == ABANDONED_MESSAGE
    assert MessageToDict(data.data) == {"code": "task_abandoned", "message": ABANDONED_MESSAGE, "retryable": True}
    assert MessageToDict(data.metadata)[EVENT_EXTENSION_URI_V1] == {"schema": f"{URI}#ExtensionError"}
    assert settled.metadata[SETTLED_REASON] == "task_abandoned"
    assert [m.message_id for m in settled.history] == [
        "t-1-said",
        settled.status.message.message_id,
    ], "what the task last said stays in history, and the error ends it"


async def test_a_task_written_within_the_timeout_is_left() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1"), idle=TIMEOUT - 5)

    assert await settler(store).settle("t-1") is None
    assert (await store.get("t-1")).status.state == TaskState.TASK_STATE_WORKING


@pytest.mark.parametrize(
    "state",
    [
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
    ],
)
async def test_a_task_waiting_for_input_or_finished_is_never_touched(state) -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1", state), idle=TIMEOUT * 100)

    assert await settler(store).settle("t-1") is None
    assert await settler(store).sweep(not_here) == []
    assert (await store.get("t-1")).status.state == state


async def test_a_task_written_while_it_is_settled_keeps_its_state(monkeypatch) -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1"), idle=TIMEOUT + 1)
    stale_tasks = InMemoryStaleTasks(store)
    read = stale_tasks.read

    async def read_then_written(task_id, timeout):
        found = await read(task_id, timeout)
        await store.save(task("t-1"), ServerCallContext())
        return found

    monkeypatch.setattr(stale_tasks, "read", read_then_written)

    assert await AbandonedTasks(stale_tasks, timeout=TIMEOUT).settle("t-1") is None
    kept = await store.get("t-1")
    assert kept.status.state == TaskState.TASK_STATE_WORKING
    assert SETTLED_REASON not in kept.metadata


async def test_the_settled_task_is_pushed() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1"), idle=TIMEOUT + 1)
    push_sender = AsyncMock()

    settled = await settler(store, push_sender=push_sender).settle("t-1")

    push_sender.send_notification.assert_awaited_once_with("t-1", settled)


async def test_the_sweep_settles_every_abandoned_task_but_one_running_here(caplog) -> None:
    store = InMemoryTaskStore()
    await stored(store, task("gone-1"), task("gone-2", routed=URI), task("here"), idle=TIMEOUT + 1)
    await stored(store, task("fresh"))

    async def running_here(task_id: str) -> bool:
        return task_id == "here"

    with caplog.at_level(logging.WARNING):
        settled = await settler(store).sweep(running_here)

    assert sorted(settled) == ["gone-1", "gone-2"]
    assert (await store.get("here")).status.state == TaskState.TASK_STATE_WORKING
    assert (await store.get("fresh")).status.state == TaskState.TASK_STATE_WORKING
    assert "Task here has written nothing" in caplog.text


class LockedStaleTasks(InMemoryStaleTasks):
    """The in-memory store behind a sweep lock that another server may hold; it records what happens inside."""

    def __init__(self, store: InMemoryTaskStore, *, held_elsewhere: bool = False) -> None:
        super().__init__(store)
        self.held_elsewhere = held_elsewhere
        self.log: list[str] = []

    @asynccontextmanager
    async def sweep_lock(self):
        self.log.append("lock")
        try:
            yield not self.held_elsewhere
        finally:
            self.log.append("unlock")

    async def candidates(self, timeout: float) -> list[str]:
        self.log.append("candidates")
        return await super().candidates(timeout)

    async def write(self, stale, settled, event) -> bool:
        self.log.append("write")
        return await super().write(stale, settled, event)


async def test_a_sweep_holds_the_lock_from_the_candidate_query_to_the_last_settlement() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("gone-1"), task("gone-2"), idle=TIMEOUT + 1)
    stale_tasks = LockedStaleTasks(store)

    settled = await AbandonedTasks(stale_tasks, timeout=TIMEOUT).sweep(not_here)

    assert sorted(settled) == ["gone-1", "gone-2"]
    assert stale_tasks.log == ["lock", "candidates", "write", "write", "unlock"]


async def test_a_sweep_does_nothing_while_another_server_holds_the_lock(caplog) -> None:
    store = InMemoryTaskStore()
    await stored(store, task("gone"), idle=TIMEOUT + 1)
    stale_tasks = LockedStaleTasks(store, held_elsewhere=True)

    with caplog.at_level(logging.DEBUG, logger="aion.server.tasks.abandoned"):
        settled = await AbandonedTasks(stale_tasks, timeout=TIMEOUT).sweep(not_here)

    assert settled == []
    assert stale_tasks.log == ["lock", "unlock"]
    assert (await store.get("gone")).status.state == TaskState.TASK_STATE_WORKING
    assert "this sweep is skipped" in caplog.text


async def test_the_lock_is_released_when_the_sweep_fails() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("gone"), idle=TIMEOUT + 1)
    stale_tasks = LockedStaleTasks(store)

    async def failing(_task_id: str) -> bool:
        raise RuntimeError("the sweep failed")

    with pytest.raises(RuntimeError):
        await AbandonedTasks(stale_tasks, timeout=TIMEOUT).sweep(failing)

    assert stale_tasks.log == ["lock", "candidates", "unlock"]


async def test_settling_one_task_takes_no_lock() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("gone"), idle=TIMEOUT + 1)
    stale_tasks = LockedStaleTasks(store, held_elsewhere=True)

    assert await AbandonedTasks(stale_tasks, timeout=TIMEOUT).settle("gone") is not None
    assert "lock" not in stale_tasks.log


async def test_a_zero_timeout_settles_nothing() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1"), idle=TIMEOUT * 100)
    abandoned = settler(store, timeout=0)

    assert not abandoned.enabled
    assert await abandoned.settle("t-1") is None
    assert await abandoned.sweep(not_here) == []
    await asyncio.wait_for(abandoned.run(not_here), 1)


@pytest.mark.parametrize(("timeout", "interval"), [(3600, 60), (120, 60), (30, 15), (2, 1)])
def test_the_sweep_runs_every_minute_or_at_half_a_small_timeout(timeout, interval) -> None:
    assert settler(InMemoryTaskStore(), timeout=timeout).interval == interval


async def test_the_background_sweep_settles_without_a_request() -> None:
    store = InMemoryTaskStore()
    await stored(store, task("t-1"), idle=1)
    running = asyncio.create_task(settler(store, timeout=0.2).run(not_here))
    try:
        for _ in range(50):
            if (await store.get("t-1")).status.state == TaskState.TASK_STATE_FAILED:
                break
            await asyncio.sleep(0.05)
    finally:
        running.cancel()

    assert (await store.get("t-1")).status.state == TaskState.TASK_STATE_FAILED


def test_the_timeout_defaults_to_an_hour_and_reads_the_environment(monkeypatch) -> None:
    assert AppSettings().stale_task_timeout == 3600
    monkeypatch.setenv("AION_STALE_TASK_TIMEOUT", "0")
    assert AppSettings().stale_task_timeout == 0


@pytest.mark.parametrize("value", ["-1", "an hour", "nan"])
def test_a_timeout_that_is_not_a_number_of_seconds_stops_the_startup(monkeypatch, value) -> None:
    monkeypatch.setenv("AION_STALE_TASK_TIMEOUT", value)

    with pytest.raises(ValidationError, match="AION_STALE_TASK_TIMEOUT"):
        AppSettings()
