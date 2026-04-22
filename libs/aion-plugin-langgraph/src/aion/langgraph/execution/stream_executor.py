"""Per-call stream executor for LangGraph astream."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.shared.logging import get_logger
from aion.shared.types import ArtifactId

from .event_converter import LangGraphA2AConverter
from .event_preprocessor import LangGraphEventPreprocessor

logger = get_logger()

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

STREAM_MODES = ["values", "messages", "custom", "updates"]


@dataclass(frozen=True)
class StreamResult:
    """Accumulated state after one stream cycle.

    delta_text — concatenated text extracted from STREAM_DELTA chunks.
        Non-empty only when the graph streamed AIMessageChunks without
        a subsequent complete TaskStatusUpdateEvent message.
    """

    delta_text: str


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
        self._delta_text: str = ""

    @property
    def result(self) -> StreamResult:
        """Accumulated state. Valid after `execute()` iteration is complete."""
        return StreamResult(delta_text=self._delta_text)

    async def execute(
        self,
        inputs: Any,
        config: dict[str, Any],
        runtime_context: Optional[Any] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run astream and yield A2A events directly.

        Tracks streaming text and final-message presence as events pass through.

        Args:
            inputs: astream input — state dict or Command object (for resume).
            config: LangGraph config dict (thread_id, etc.).
            runtime_context: Optional AionContext passed to graph.astream() as runtime context.

        Yields:
            A2A AgentEvent objects.
        """
        kwargs = {}
        if runtime_context is not None:
            kwargs["context"] = runtime_context

        async for event_type, event_data in self._graph.astream(
            inputs, config, stream_mode=STREAM_MODES, **kwargs
        ):
            if event_type == "messages":
                event_data, _ = event_data

            if self._preprocessor:
                self._preprocessor.process(event_type, event_data)

            a2a_events = self._converter.convert(event_type, event_data)
            for a2a_event in a2a_events:
                self._track(a2a_event)
                yield a2a_event

    def _track(self, a2a_event: AgentEvent) -> None:
        """Update internal state based on the outgoing event."""
        if isinstance(a2a_event, TaskArtifactUpdateEvent):
            if a2a_event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value:
                for part in a2a_event.artifact.parts:
                    if part.text:
                        self._delta_text += part.text
            return

        if isinstance(a2a_event, TaskStatusUpdateEvent):
            if a2a_event.status.message is not None:
                self._delta_text = ""
