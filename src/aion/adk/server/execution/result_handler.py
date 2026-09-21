"""Handles ADK execution result: produces pre-terminal A2A events."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

from aion.core.a2a import A2AOutbox
from aion.server.a2a.outbox import outbox_message, outbox_task
from .event_converter import ADKToA2AEventConverter
from .stream_executor import ADKStreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Task | Message

logger = logging.getLogger(__name__)


class ADKExecutionResultHandler:
    """Processes the stream result into pre-terminal A2A events.

    Called by ADKExecutor after the stream cycle completes, before
    the final complete/error event is emitted.

    Reads `a2a_outbox` from the ADK session state and applies it through
    `aion.server.a2a.outbox`, which is where what the server does with an
    outbox is defined — for this adapter and the LangGraph one alike. The
    result is one `Message` or one `Task`, handed on as itself for the
    execution pipeline to save into the task record.

    Falls back to streaming accumulated text if no outbox is present.

    Subclass and override `handle` to extend or replace the default logic.
    """

    def handle(
            self,
            stream_result: ADKStreamResult,
            converter: ADKToA2AEventConverter,
            session: Any = None,
            context: "RequestContext | None" = None,
            task_id: str = "",
            context_id: str = "",
    ) -> list[AgentEvent]:
        """Produce A2A events based on execution result.

        Checks `a2a_outbox` in the final ADK session state. If present and
        parseable as a Task or Message, returns that object for the pipeline to
        merge into the task. Otherwise falls back to closing any pending
        STREAM_DELTA and emitting accumulated delta text.

        Args:
            stream_result: Accumulated state from the stream cycle.
            converter: Active converter holding stream state for this execution.
            session: ADK Session after stream completion (provides final state).
            context: A2A request context (current_task, task_id, etc.). Unused
                by the outbox path — the payload is merged by the task manager,
                not by editing the request's copy of the task — and kept for
                subclasses that override this method.
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
                    logger.debug("Result via outbox: %d event(s)", len(result))
                    return result

        logger.debug("Result via stream fallback")
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
            return self._handle_outbox_message(parsed.message, task_id, context_id)

        if parsed.task is not None:
            return self._handle_outbox_task(parsed.task, context, task_id, context_id)

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


__all__ = ["ADKExecutionResultHandler"]
