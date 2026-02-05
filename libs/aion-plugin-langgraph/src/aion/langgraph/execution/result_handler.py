"""Handles execution result: produces terminal event and prepares task updates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.types import Part, TextPart
from aion.shared.agent.adapters import MessageEvent

from .stream_executor import StreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.shared.agent.adapters import ExecutionEvent, ExecutionSnapshot


class ExecutionResultHandler:
    """Processes execution result into a terminal event and task side-effects.

    Receives the stream result and the authoritative snapshot (from aget_state).
    Determines whether an additional response event needs to be emitted before
    CompleteEvent / InterruptEvent, and applies any task updates from the
    graph state (e.g. a2a_outbox).

    Default priority:
        1. A final non-streaming MessageEvent was already yielded > empty list.
        2. Accumulated text from streaming chunks exists > MessageEvent fallback.
        3. Otherwise > empty list.

    Subclass and override `handle` to add custom logic - e.g. reading
    a2a_outbox from `snapshot.state`, patching the Task, merging messages.
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
            Events to emit before Complete/Interrupt. Empty list if the
            stream already delivered a complete message.
        """
        if stream_result.has_final_message:
            return []

        if stream_result.accumulated_text:
            return [
                MessageEvent(
                    content=[Part(root=TextPart(text=stream_result.accumulated_text))],
                    role="assistant",
                    is_streaming=False,
                )
            ]

        return []
