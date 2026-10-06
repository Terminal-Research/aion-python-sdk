"""Tests for tearing down an ActiveTask that is no longer executing anything.

A task that stops at INPUT_REQUIRED hands control back without reaching a
terminal state, so the SDK never shuts its queues: the producer and consumer
park forever and the registry keeps the whole conversation alive. Across
several instances the answer usually lands on a different pod, so the original
object is never resumed and never finished either.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from a2a.server.agent_execution import RequestContext
from a2a.server.cluster.task_store import ConcurrentTaskModificationError

from aion.server.agent.execution.active_task_registry import AionActiveTaskRegistry
from aion.server.agent.execution.scope import init_execution_scope
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.task_manager import AionTaskManager

TASK_ID = str(uuid.uuid4())
CONTEXT_ID = "ctx"


def _registry() -> AionActiveTaskRegistry:
    """Build a registry over a stand-in store."""
    return AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=AsyncMock(),
        push_sender=None,
    )


def _registered(registry: AionActiveTaskRegistry) -> MagicMock:
    """Register a stand-in ActiveTask under both of the registry's keys."""
    active_task = MagicMock(task_id=TASK_ID, aclose=AsyncMock())
    active_task._is_finished = asyncio.Event()
    registry._active_tasks[TASK_ID] = active_task
    registry._task_managers[TASK_ID] = active_task._task_manager
    return active_task


@pytest.mark.anyio
async def test_interrupted_task_is_closed_and_forgotten() -> None:
    """The interrupt signal closes the object and empties both registry maps."""
    registry = _registry()
    active_task = _registered(registry)

    registry._on_task_interrupted(TASK_ID)
    await asyncio.gather(*registry._interruption_tasks)

    active_task.aclose.assert_awaited_once()

    # The SDK reports cleanup by callback; that is what empties the maps.
    await registry._remove_task_for_incarnation(active_task)
    assert TASK_ID not in registry._active_tasks
    assert TASK_ID not in registry._task_managers


@pytest.mark.anyio
async def test_one_interrupt_schedules_one_teardown() -> None:
    """Repeated signals for the same object must not close it twice."""
    registry = _registry()
    active_task = _registered(registry)

    registry._on_task_interrupted(TASK_ID)
    registry._on_task_interrupted(TASK_ID)
    await asyncio.gather(*registry._interruption_tasks)

    active_task.aclose.assert_awaited_once()


@pytest.mark.anyio
async def test_shutdown_writes_nothing_for_a_task_owned_elsewhere() -> None:
    """A slow shutdown must not overwrite work another instance moved on.

    The settlement write is versioned like any other, so it fails; the
    shutdown has to survive that rather than report an error it cannot act on.
    """
    registry = _registry()
    task_manager = MagicMock(task_id=TASK_ID)
    task_manager.get_task = AsyncMock(
        return_value=Task(
            id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
    )
    task_manager.save_task_event = AsyncMock(side_effect=ConcurrentTaskModificationError(TASK_ID))
    registry._task_managers[TASK_ID] = task_manager

    await registry.aclose()

    task_manager.save_task_event.assert_awaited_once()
    assert registry._task_managers == {}


class _AskingAgent:
    """An agent that announces its task, asks the user a question, and returns."""

    def __init__(self, task_id: str) -> None:
        """Bind the agent to the task it will announce."""
        self.task_id = task_id

    async def execute(self, context, event_queue) -> None:
        """Produce the two events a turn ending in a question produces."""
        await event_queue.enqueue_event(
            Task(
                id=self.task_id,
                context_id=CONTEXT_ID,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=self.task_id,
                context_id=CONTEXT_ID,
                status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            )
        )

    async def cancel(self, context, event_queue) -> None:
        """Nothing to cancel in this agent."""


@pytest.mark.anyio
async def test_a_real_interrupted_turn_leaves_the_registry_empty() -> None:
    """The whole teardown, driven by an agent rather than by a signal.

    The focused tests above each drive one link of the chain: the interrupt
    signal, the close, the release. This one runs a turn end to end and looks
    at what is left over - the maps, and the background tasks the SDK spawns
    per execution, which are what a leak here would actually cost.
    """
    task_id = str(uuid.uuid4())
    store = InMemoryTaskStore(owner_resolver=lambda _context: "owner")
    registry = AionActiveTaskRegistry(
        agent_executor=_AskingAgent(task_id),
        task_store=store,
        push_sender=None,
    )
    init_execution_scope()
    call_context = Mock()

    active_task = await registry.get_or_create(
        task_id,
        call_context=call_context,
        context_id=CONTEXT_ID,
        create_task_if_missing=True,
    )
    request = RequestContext(
        call_context=call_context,
        task_id=task_id,
        context_id=CONTEXT_ID,
    )
    async for _event in active_task.subscribe(request=request):
        pass
    await _drain_pending()

    stored = await store.get(task_id, call_context)
    assert stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert registry._active_tasks == {}
    assert registry._task_managers == {}
    assert registry._interruption_tasks_by_id == {}
    assert active_task._is_finished.is_set()
    assert _background_tasks_for(task_id) == []


def _background_tasks_for(task_id: str) -> list[str]:
    """Return the names of unfinished asyncio tasks the SDK spawned for a task."""
    return [
        task.get_name()
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().endswith(task_id)
    ]


async def _drain_pending() -> None:
    """Let the callbacks the SDK schedules run before anything is asserted."""
    for _ in range(10):
        await asyncio.sleep(0)


class _AskThenAnswerAgent:
    """An agent that asks a question on one turn and answers it on the next.

    The two turns are what the server actually produces: the first opens the
    task and stops at INPUT_REQUIRED, the second is a resume, which the request
    executor acknowledges with a bare WORKING status before the agent speaks.
    """

    def __init__(self, task_id: str) -> None:
        """Bind the agent to the task it will announce."""
        self.task_id = task_id
        self.turns = 0

    async def execute(self, context, event_queue) -> None:
        """Ask on the first turn, answer on the second."""
        self.turns += 1
        if self.turns == 1:
            opened = Task(
                id=self.task_id,
                context_id=CONTEXT_ID,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
            opened.history.append(context.message)
            await event_queue.enqueue_event(opened)
            await event_queue.enqueue_event(
                _status_event(
                    self.task_id,
                    TaskState.TASK_STATE_INPUT_REQUIRED,
                    _text_message("question", Role.ROLE_AGENT, "Which environment?"),
                )
            )
            return

        await event_queue.enqueue_event(
            _status_event(self.task_id, TaskState.TASK_STATE_WORKING)
        )
        await event_queue.enqueue_event(
            _status_event(
                self.task_id,
                TaskState.TASK_STATE_COMPLETED,
                _text_message("answer", Role.ROLE_AGENT, "Got it - staging."),
            )
        )

    async def cancel(self, context, event_queue) -> None:
        """Nothing to cancel in this agent."""


def _text_message(message_id: str, role: Role, text: str) -> Message:
    """Build a message carrying a single text part."""
    return Message(
        message_id=message_id,
        context_id=CONTEXT_ID,
        role=role,
        parts=[Part(text=text)],
    )


def _status_event(
    task_id: str, state: TaskState, message: Message | None = None
) -> TaskStatusUpdateEvent:
    """Build a status event for the task under test."""
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(task_id=task_id, context_id=CONTEXT_ID, status=status)


async def _run_turn(
    registry: AionActiveTaskRegistry,
    task_id: str,
    call_context,
    message: Message,
):
    """Drive one turn end to end, returning the object and what it streamed."""
    active_task = await registry.get_or_create(
        task_id,
        call_context=call_context,
        context_id=CONTEXT_ID,
        create_task_if_missing=True,
        initial_message=message,
    )
    request = RequestContext(
        call_context=call_context,
        request=SendMessageRequest(message=message),
        task_id=task_id,
        context_id=CONTEXT_ID,
    )
    events = [event async for event in active_task.subscribe(request=request)]
    await _drain_pending()
    return active_task, events


def _message_texts(messages) -> list[str]:
    """Flatten the text parts of each message."""
    return ["".join(part.text for part in message.parts) for message in messages]


@pytest.mark.anyio
async def test_resuming_an_interrupted_task_delivers_the_answer(caplog) -> None:
    """The turn after INPUT_REQUIRED runs to COMPLETED with its answer.

    A resume saves the incoming user message before the first event of the new
    turn is applied, so the task is written once more in the state it was
    resumed from. That write must not be read as a fresh interrupt: closing the
    ActiveTask here would shut the queues under the turn that just started, and
    the agent's answer and its terminal state would be dropped.
    """
    caplog.set_level(logging.WARNING)
    task_id = str(uuid.uuid4())
    store = InMemoryTaskStore(owner_resolver=lambda _context: "owner")
    agent = _AskThenAnswerAgent(task_id)
    registry = AionActiveTaskRegistry(
        agent_executor=agent,
        task_store=store,
        push_sender=None,
    )
    init_execution_scope()
    call_context = Mock()

    await _run_turn(
        registry,
        task_id,
        call_context,
        _text_message("ask", Role.ROLE_USER, "ask"),
    )
    asked = await store.get(task_id, call_context)
    assert asked.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    resumed_task, streamed = await _run_turn(
        registry,
        task_id,
        call_context,
        _text_message("reply", Role.ROLE_USER, "staging please"),
    )

    stored = await store.get(task_id, call_context)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    assert _message_texts([stored.status.message]) == ["Got it - staging."]
    assert _message_texts(stored.history) == [
        "ask",
        "Which environment?",
        "staging please",
    ]
    assert agent.turns == 2

    assert registry._active_tasks == {}
    assert registry._task_managers == {}
    assert registry._interruption_tasks_by_id == {}
    assert resumed_task._is_finished.is_set()
    assert _background_tasks_for(task_id) == []
    # The events of the resumed turn reached the subscriber rather than a
    # queue that had already been shut: this is what the live failure loses.
    assert [event.status.state for event in streamed] == [
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    ]
    assert _dropped_event_warnings(caplog) == []


def _dropped_event_warnings(caplog) -> list[str]:
    """Return the SDK's reports of events it could not enqueue.

    The live symptom of a queue shut under a running turn: the agent's answer
    and its terminal state are refused by the closed queue and logged as
    dropped instead of reaching anyone.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and "Event dropped" in record.getMessage()
    ]
