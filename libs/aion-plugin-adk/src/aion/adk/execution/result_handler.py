"""Handles ADK execution result: produces pre-terminal A2A events."""

from __future__ import annotations

import copy
from typing import Any, TYPE_CHECKING

from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.shared.types import A2AOutbox
from .event_converter import ADKToA2AEventConverter
from .stream_executor import StreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent


class ADKExecutionResultHandler:
    """Processes the stream result into pre-terminal A2A events.

    Called by ADKExecutor after the stream cycle completes, before
    the final complete/error event is emitted.

    Reads `a2a_outbox` from the ADK session state and applies it to the
    current a2a Task.
    Falls back to streaming accumulated text if no outbox is present.

    Subclass and override `handle` to extend or replace the default logic.
    """

    def handle(
            self,
            stream_result: StreamResult,
            converter: ADKToA2AEventConverter,
            session: Any = None,
            context: "RequestContext | None" = None,
            task_id: str = "",
            context_id: str = "",
    ) -> list[AgentEvent]:
        """Produce A2A events based on execution result.

        Checks `a2a_outbox` in the final ADK session state. If present and
        parseable as a Task or Message, emits the appropriate A2A events.
        Otherwise falls back to closing any pending STREAM_DELTA and emitting
        accumulated delta text.

        Args:
            stream_result: Accumulated state from the stream cycle.
            converter: Active converter holding stream state for this execution.
            session: ADK Session after stream completion (provides final state).
            context: A2A request context (current_task, task_id, etc.).
            task_id: Current task ID.
            context_id: Current context ID.

        Returns:
            A2A events to emit before the terminal complete/error event.
        """
        if session is not None:
            state = getattr(session, "state", None) or {}
            outbox = state.get("a2a_outbox")
            if outbox is not None:
                result = self._handle_outbox(outbox, context, task_id, context_id)
                if result is not None:
                    return result

        return converter.finalize_stream(stream_result.delta_text)

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
        if not isinstance(outbox, dict):
            return None

        try:
            parsed = A2AOutbox.model_validate(outbox)
        except Exception:
            return None

        if parsed.message is not None:
            return self._handle_outbox_message(parsed.message, context, task_id, context_id)

        if parsed.task is not None:
            return self._handle_outbox_task(parsed.task, context, task_id, context_id)

        return None

    @staticmethod
    def _handle_outbox_message(
            message: Message,
            context: "RequestContext | None",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Enforce server fields, append to history, emit working-status event."""
        message = copy.deepcopy(message)
        message.task_id = task_id
        message.context_id = context_id

        if context is not None:
            task = context.current_task
            if task is not None:
                updated = copy.deepcopy(task)
                updated.history.append(message)
                context.current_task = updated

        return [TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=message),
        )]

    @staticmethod
    def _handle_outbox_task(
            patch: Task,
            context: "RequestContext | None",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Patch current task from outbox Task.

        Merge rules:
            - id, context_id, kind, status — kept from current task (server-owned).
            - history, artifacts — extended (current + patch).
            - metadata — shallow merge; current task's keys take precedence
              (protects server-controlled keys such as aion:network).

        Emits a TaskStatusUpdateEvent for every message in patch.history and a
        TaskArtifactUpdateEvent for every artifact.
        """
        events: list[AgentEvent] = []

        patched_history = []
        for msg in patch.history:
            new_msg = copy.deepcopy(msg)
            new_msg.task_id = task_id
            new_msg.context_id = context_id
            patched_history.append(new_msg)

        if context is not None:
            current = context.current_task
            if current is not None:
                merged = copy.deepcopy(current)
                merged.history.extend(patched_history)
                merged.artifacts.extend(patch.artifacts)
                # patch overwrites current keys (preserves original merge behaviour)
                for k, v in patch.metadata.items():
                    merged.metadata[k] = v
                context.current_task = merged

        if patch.metadata:
            filtered = {k: v for k, v in patch.metadata.items() if not k.startswith("aion:")}
            if filtered:
                events.append(TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    metadata=filtered,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                ))

        for msg in patched_history:
            events.append(TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
            ))

        for artifact in patch.artifacts:
            events.append(TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=artifact,
                append=False,
                last_chunk=True,
            ))

        return events


__all__ = ["ADKExecutionResultHandler"]
