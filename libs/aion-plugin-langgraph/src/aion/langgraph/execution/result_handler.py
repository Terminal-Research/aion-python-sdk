"""Handles execution result: produces terminal event and prepares task updates."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from a2a.types import Message, Part, Role, Task, TextPart
from aion.shared.agent.adapters import ArtifactEvent, MessageEvent, StateUpdateEvent

from .stream_executor import StreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.shared.agent.adapters import ExecutionEvent, ExecutionSnapshot


class ExecutionResultHandler:
    """Processes execution result into a terminal event and task side-effects.

    Reads `a2a_outbox` from the graph's final state and applies it to the
    current a2a Task. If no outbox is present, falls back to streaming
    accumulated text.

    Priority:
        1. `a2a_outbox` in snapshot.state > Message or Task (authoritative).
        2. A final non-streaming MessageEvent was already yielded > empty list.
        3. Accumulated text from streaming chunks > MessageEvent fallback.
        4. Otherwise > empty list.

    Subclass and override `handle` to extend or replace the default logic.
    """

    def handle(
            self,
            stream_result: StreamResult,
            snapshot: ExecutionSnapshot,
            context: RequestContext,
    ) -> list[ExecutionEvent]:
        """Produce events based on execution result.

        Args:
            stream_result: Accumulated state from the stream cycle.
            snapshot: Authoritative execution snapshot from aget_state.
            context: A2A request context (current_task, task_id, etc.).

        Returns:
            Events to emit before Complete/Interrupt.
        """
        outbox = snapshot.state.get("a2a_outbox")
        if outbox is not None:
            result = self._handle_outbox(outbox, context)
            if result is not None:
                return result

        # Fallback: no outbox or outbox type not yet handled
        if stream_result.has_final_message:
            return []

        if stream_result.accumulated_text:
            return [
                MessageEvent(
                    content=[Part(root=TextPart(text=stream_result.accumulated_text))],
                    role="agent",
                    is_chunk=False,
                    is_last_chunk=False,
                )
            ]

        return []

    def _handle_outbox(
            self,
            outbox: Any,
            context: RequestContext,
    ) -> list[ExecutionEvent] | None:
        """Route outbox to the appropriate handler based on type.

        Returns None if the outbox type is not recognized — caller
        falls through to fallback logic.
        """
        message = self._parse_message(outbox)
        if message is not None:
            return self._handle_outbox_message(message, context)

        task = self._parse_task(outbox)
        if task is not None:
            return self._handle_outbox_task(task, context)

        return None

    def _handle_outbox_message(
            self,
            message: Message,
            context: RequestContext,
    ) -> list[ExecutionEvent]:
        """Process outbox Message: enforce server fields, append to history, emit event."""
        # Enforce server-owned fields
        message = message.model_copy(update={
            "task_id": context.task_id,
            "context_id": context.context_id,
        })
        # Append to current task history
        task = context.current_task
        if task is not None:
            history = list(task.history or [])
            history.append(message)
            context.current_task = task.model_copy(update={"history": history})

        return [MessageEvent(
            content=message.parts,
            role=message.role.value,
            is_chunk=False,
            is_last_chunk=False,
        )]

    def _handle_outbox_task(
            self,
            patch: Task,
            context: RequestContext,
    ) -> list[ExecutionEvent]:
        """Patch current task from outbox Task.

        Merge rules:
            - id, context_id, kind, status — kept from current task (server-owned).
            - history, artifacts — extended (current + patch).
            - metadata — shallow merge; current task's keys take precedence
              (protects server-controlled keys such as aion:network).

        Emits a MessageEvent for every message in patch.history (order preserved)
        and an ArtifactEvent if patch carries artifacts.
        """
        task = context.current_task
        if task is None:
            return []

        # Enforce server-owned fields on patch messages before merging
        patched_history = [
            msg.model_copy(update={
                "task_id": context.task_id,
                "context_id": context.context_id,
            })
            for msg in (patch.history or [])
        ]

        merged = task.model_copy(update={
            "history": list(task.history or []) + patched_history,
            "artifacts": list(task.artifacts or []) + list(patch.artifacts or []),
            "metadata": {**(task.metadata or {}), **(patch.metadata or {})},
        })
        context.current_task = merged

        events: list[ExecutionEvent] = []

        # Emit metadata delta first — before messages — so task_updater
        # persists it without clearing the current status message.
        if patch.metadata:
            events.append(StateUpdateEvent(data={"task_metadata": patch.metadata}))

        for msg in patched_history:
            events.append(MessageEvent(
                content=msg.parts,
                role=msg.role.value,
                is_chunk=False,
                is_last_chunk=False,
            ))

        # Emit one ArtifactEvent per artifact
        for artifact in (patch.artifacts or []):
            events.append(ArtifactEvent(
                artifact=artifact,
                append=False,
                is_last_chunk=True,
            ))

        return events

    @staticmethod
    def _parse_message(outbox: Any) -> Message | None:
        """Parse outbox into a Message if possible.

        Handles both typed Message instances and raw dicts
        (e.g. after checkpointer serialization).
        """
        if isinstance(outbox, Message):
            return outbox
        if isinstance(outbox, dict) and outbox.get("kind") == "message":
            return Message.model_validate(outbox)
        return None

    @staticmethod
    def _parse_task(outbox: Any) -> Task | None:
        """Parse outbox into a Task if possible.

        Handles both typed Task instances and raw dicts
        (e.g. after checkpointer serialization).
        """
        if isinstance(outbox, Task):
            return outbox
        if isinstance(outbox, dict) and outbox.get("kind") == "task":
            return Task.model_validate(outbox)
        return None
