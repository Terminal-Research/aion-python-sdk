"""Handles execution result: produces terminal event and prepares task updates."""

from __future__ import annotations

import uuid
from typing import Any, TYPE_CHECKING

from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.core.a2a import A2AOutbox
from aion.server.a2a.outbox import outbox_message, outbox_task
from .stream_executor import StreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.server.agent.adapters import ExecutionSnapshot

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Task | Message


class ExecutionResultHandler:
    """Processes execution result into terminal events and task side-effects.

    Reads `a2a_outbox` from the graph's final state and applies it through
    `aion.server.a2a.outbox`, which is where what the server does with an
    outbox is defined — for this adapter and the ADK one alike. If no outbox
    is present, falls back to streaming accumulated text.

    Subclass and override `handle` to extend or replace the default logic.
    """

    def handle(
            self,
            stream_result: StreamResult,
            snapshot: "ExecutionSnapshot",
            context: "RequestContext",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Produce A2A events based on execution result.

        Args:
            stream_result: Accumulated state from the stream cycle.
            snapshot: Authoritative execution snapshot from aget_state.
            context: A2A request context (current_task, task_id, etc.).
            task_id: Current task ID (used to construct A2A events).
            context_id: Current context ID (used to construct A2A events).

        Returns:
            A2A events to emit before Complete/Interrupt.
        """
        outbox = snapshot.state.get("a2a_outbox")
        if outbox is not None:
            result = self._handle_outbox(outbox, context, task_id, context_id)
            if result is not None:
                return result

        # Fallback: no outbox or outbox type not yet handled
        if stream_result.delta_text and not snapshot.requires_input():
            msg = Message(
                context_id=context_id,
                task_id=task_id,
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_AGENT,
                parts=[Part(text=stream_result.delta_text)],
            )
            return [TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
            )]

        return []

    def _handle_outbox(
            self,
            outbox: Any,
            context: "RequestContext | None",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent] | None:
        """Route outbox to the appropriate handler based on type.

        Returns None if the outbox type is not recognized — caller
        falls through to fallback logic.
        """
        if not isinstance(outbox, A2AOutbox):
            return None

        if outbox.message is not None:
            return self._handle_outbox_message(outbox.message, task_id, context_id)

        if outbox.task is not None:
            return self._handle_outbox_task(outbox.task, context, task_id, context_id)

        return None

    @staticmethod
    def _handle_outbox_message(
            message: Message,
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Return the outbox Message itself, with the server's ids enforced."""
        return [outbox_message(message, task_id=task_id, context_id=context_id)]

    @staticmethod
    def _handle_outbox_task(
            patch: Task,
            context: "RequestContext | None",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Return the request's task with the outbox Task patch applied."""
        return [
            outbox_task(
                patch,
                current=context.current_task if context is not None else None,
                task_id=task_id,
                context_id=context_id,
            )
        ]
