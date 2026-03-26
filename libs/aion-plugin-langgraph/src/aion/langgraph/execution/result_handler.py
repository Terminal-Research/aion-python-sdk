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

from aion.shared.types import A2AOutbox
from .stream_executor import StreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.shared.agent.adapters import ExecutionSnapshot

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent


class ExecutionResultHandler:
    """Processes execution result into terminal events and task side-effects.

    Reads `a2a_outbox` from the graph's final state and applies it to the
    current a2a Task. If no outbox is present, falls back to streaming
    accumulated text.

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
            context: "RequestContext",
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
            return self._handle_outbox_message(outbox.message, context, task_id, context_id)

        if outbox.task is not None:
            return self._handle_outbox_task(outbox.task, context, task_id, context_id)

        return None

    def _handle_outbox_message(
            self,
            message: Message,
            context: "RequestContext",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Process outbox Message: enforce server fields, append to history, emit event."""
        new_msg = Message()
        new_msg.CopyFrom(message)
        new_msg.task_id = task_id
        new_msg.context_id = context_id

        task = context.current_task
        if task is not None:
            updated_task = Task()
            updated_task.CopyFrom(task)
            updated_task.history.append(new_msg)
            context.current_task = updated_task

        return [TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=new_msg),
        )]

    def _handle_outbox_task(
            self,
            patch: Task,
            context: "RequestContext",
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
        task = context.current_task
        if task is None:
            return []

        patched_history = []
        for msg in patch.history:
            new_msg = Message()
            new_msg.CopyFrom(msg)
            new_msg.task_id = task_id
            new_msg.context_id = context_id
            patched_history.append(new_msg)

        merged = Task()
        merged.CopyFrom(task)
        merged.history.extend(patched_history)
        merged.artifacts.extend(patch.artifacts)
        for k, v in patch.metadata.items():
            merged.metadata[k] = v
        context.current_task = merged

        events: list[AgentEvent] = []

        # Emit metadata delta first so task_updater persists it without
        # clearing the current status message.
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

        for artifact in (patch.artifacts or []):
            events.append(TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=artifact,
                append=False,
                last_chunk=True,
            ))

        return events

