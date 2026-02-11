"""Per-call stream executor for LangGraph astream."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent, TextPart
from aion.shared.logging import get_logger
from aion.shared.types import ArtifactId

from .a2a_converter import LangGraphA2AConverter
from .event_preprocessor import LangGraphEventPreprocessor

logger = get_logger()

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

STREAM_MODES = ["values", "messages", "custom", "updates"]


@dataclass(frozen=True)
class StreamResult:
    """Accumulated state after one stream cycle.

    accumulated_text — concatenated text extracted from streaming message chunks.
        Non-empty only when the graph streamed AIMessageChunks but did not
        emit a final complete message.
    has_final_message — True if at least one complete (non-streaming) MessageEvent
        was yielded during the cycle. When True, ResponseAssembler skips fallback.
    """

    accumulated_text: str
    has_final_message: bool


class StreamExecutor:
    """Executes one astream cycle against a compiled LangGraph graph.

    Lifecycle: instantiate > iterate `execute()` > read `result`.
    Created fresh per stream/resume call. Does not know about response
    assembly or state persistence.
    """

    def __init__(
        self,
        compiled_graph: Any,
        converter: LangGraphA2AConverter,
        preprocessor: Optional[LangGraphEventPreprocessor] = None,
    ):
        self._graph = compiled_graph
        self._converter = converter
        self._preprocessor = preprocessor
        self._accumulated_text: str = ""
        self._has_final_message: bool = False

    @property
    def result(self) -> StreamResult:
        """Accumulated state. Valid after `execute()` iteration is complete."""
        return StreamResult(
            accumulated_text=self._accumulated_text,
            has_final_message=self._has_final_message,
        )

    async def execute(
        self,
        inputs: Any,
        config: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """Run astream and yield A2A events directly.

        Tracks streaming text and final-message presence as events pass through.

        Args:
            inputs: astream input — state dict or Command object (for resume).
            config: LangGraph config dict (thread_id, etc.).

        Yields:
            A2A AgentEvent objects.
        """
        async for event_type, event_data in self._graph.astream(
            inputs, config, stream_mode=STREAM_MODES
        ):
            if event_type == "messages":
                event_data, metadata = event_data
            else:
                metadata = None

            if self._preprocessor:
                self._preprocessor.process(event_type, event_data)

            a2a_events = self._converter.convert(event_type, event_data, metadata)
            for a2a_event in a2a_events:
                self._track(a2a_event)
                yield a2a_event

    def _track(self, a2a_event: AgentEvent) -> None:
        """Update internal state based on the outgoing event."""
        if isinstance(a2a_event, TaskArtifactUpdateEvent):
            if a2a_event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value:
                for part in (a2a_event.artifact.parts or []):
                    if isinstance(part.root, TextPart):
                        self._accumulated_text += part.root.text
            return

        if isinstance(a2a_event, TaskStatusUpdateEvent):
            if a2a_event.status.message is not None:
                self._has_final_message = True
