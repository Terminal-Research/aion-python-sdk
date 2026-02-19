"""Handles ADK execution result: produces pre-terminal A2A events."""

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from .event_converter import ADKToA2AEventConverter
from .stream_executor import StreamResult

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent


class ADKExecutionResultHandler:
    """Processes the stream result into pre-terminal A2A events.

    Called by ADKExecutor after the stream cycle completes, before
    the final complete/error event is emitted.

    Subclass and override `handle` to extend or replace the default logic
    (e.g. reading ADK session state for structured outbox data).
    """

    def handle(
            self,
            stream_result: StreamResult,
            converter: ADKToA2AEventConverter,
    ) -> list[AgentEvent]:
        """Produce A2A events based on execution result.

        Default behaviour: close any open STREAM_DELTA and emit accumulated
        streaming text as a working status message if the agent stream ended
        without a closing non-partial event.

        Args:
            stream_result: Accumulated state from the stream cycle.
            converter: Active converter holding stream state for this execution.

        Returns:
            A2A events to emit before the terminal complete/error event.
        """
        return converter.convert_pending_stream(stream_result.delta_text)


__all__ = ["ADKExecutionResultHandler"]
