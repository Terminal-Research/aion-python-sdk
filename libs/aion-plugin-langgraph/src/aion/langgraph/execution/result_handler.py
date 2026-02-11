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
    TextPart,
)

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

    Priority:
        1. `a2a_outbox` in snapshot.state > Message or Task (authoritative).
        2. A final non-streaming message was already yielded > empty list.
        3. Accumulated text from streaming chunks > fallback message event.
        4. Otherwise > empty list.

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
        if stream_result.has_final_message:
            return []

        if stream_result.accumulated_text:
            msg = Message(
                context_id=context_id,
                task_id=task_id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=TextPart(text=stream_result.accumulated_text))],
            )
            return [TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=False,
                status=TaskStatus(state=TaskState.working, message=msg),
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
        message = self._parse_message(outbox)
        if message is not None:
            return self._handle_outbox_message(message, context, task_id, context_id)

        task = self._parse_task(outbox)
        if task is not None:
            return self._handle_outbox_task(task, context, task_id, context_id)

        return None

    def _handle_outbox_message(
            self,
            message: Message,
            context: "RequestContext",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Process outbox Message: enforce server fields, append to history, emit event."""
        message = message.model_copy(update={
            "task_id": task_id,
            "context_id": context_id,
        })
        task = context.current_task
        if task is not None:
            history = list(task.history or [])
            history.append(message)
            context.current_task = task.model_copy(update={"history": history})

        return [TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            final=False,
            status=TaskStatus(state=TaskState.working, message=message),
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

        patched_history = [
            msg.model_copy(update={
                "task_id": task_id,
                "context_id": context_id,
            })
            for msg in (patch.history or [])
        ]

        merged = task.model_copy(update={
            "history": list(task.history or []) + patched_history,
            "artifacts": list(task.artifacts or []) + list(patch.artifacts or []),
            "metadata": {**(task.metadata or {}), **(patch.metadata or {})},
        })
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
                    final=False,
                    metadata=filtered,
                    status=TaskStatus(state=TaskState.working),
                ))

        for msg in patched_history:
            events.append(TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=False,
                status=TaskStatus(state=TaskState.working, message=msg),
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

    @staticmethod
    def _parse_message(outbox: Any) -> Message | None:
        if isinstance(outbox, Message):
            return outbox
        if isinstance(outbox, dict) and outbox.get("kind") == "message":
            return Message.model_validate(outbox)
        return None

    @staticmethod
    def _parse_task(outbox: Any) -> Task | None:
        if isinstance(outbox, Task):
            return outbox
        if isinstance(outbox, dict) and outbox.get("kind") == "task":
            return Task.model_validate(outbox)
        return None
