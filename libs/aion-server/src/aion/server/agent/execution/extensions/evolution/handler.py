"""Stub ExtensionTaskHandler for the evolution A2A extension.

First increment: only proves that a task routed to EVOLUTION_EXTENSION_URI_V1
is handled here instead of the agent's framework adapter. Does not yet drive
the real toolkit (aion-toolkit-behaviour-evolution-python) - stream()/resume()
emit a placeholder status and complete immediately. See §4-4.5 of
Notes/WorkPlans/aion-core-extension-routing-integration.md for the follow-up
plan (directive/tools/events mapping, Task.metadata persistence, push/PR
control) that replaces this stub incrementally.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from a2a.types import Message, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils.errors import UnsupportedOperationError
from aion.core.constants.a2a import EVOLUTION_EXTENSION_URI_V1

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.core.config.models import AgentConfig


class EvolutionTaskHandler:
    """Routes evolution-extension tasks away from the framework adapter.

    Unconditionally available for now (no real toolkit dependency yet, so
    nothing to gate on). Cancellation is not yet supported - the toolkit-driven
    run doesn't exist yet, so there is nothing to interrupt.
    """

    uri = EVOLUTION_EXTENSION_URI_V1

    async def is_available(self, config: "AgentConfig") -> bool:
        return True

    async def stream(self, context: "RequestContext") -> AsyncIterator[TaskStatusUpdateEvent]:
        yield self._working_event(context, text="Evolution extension received the request.")
        yield self._terminal_event(context, state=TaskState.TASK_STATE_COMPLETED)

    async def resume(self, context: "RequestContext") -> AsyncIterator[TaskStatusUpdateEvent]:
        yield self._terminal_event(context, state=TaskState.TASK_STATE_COMPLETED)

    async def cancel(self, context: "RequestContext") -> None:
        raise UnsupportedOperationError()

    @staticmethod
    def _working_event(context: "RequestContext", *, text: str) -> TaskStatusUpdateEvent:
        task = context.current_task
        message = Message(
            context_id=task.context_id,
            task_id=task.id,
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            parts=[Part(text=text)],
        )
        return TaskStatusUpdateEvent(
            task_id=task.id,
            context_id=task.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=message),
        )

    @staticmethod
    def _terminal_event(context: "RequestContext", *, state: TaskState.ValueType) -> TaskStatusUpdateEvent:
        task = context.current_task
        return TaskStatusUpdateEvent(
            task_id=task.id,
            context_id=task.context_id,
            status=TaskStatus(state=state),
        )
