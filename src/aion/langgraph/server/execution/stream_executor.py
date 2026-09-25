"""Per-call stream executor for LangGraph astream."""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.core.a2a import ArtifactId

from .event_converter import LangGraphA2AConverter
from .event_preprocessor import LangGraphEventPreprocessor

logger = logging.getLogger(__name__)

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

STREAM_MODES = ["values", "messages", "custom", "updates"]

OUTBOX_KEY = "a2a_outbox"


@dataclass(frozen=True)
class StreamResult:
    """Accumulated state after one stream cycle.

    delta_text — concatenated text extracted from STREAM_DELTA chunks.
        Non-empty only when the graph streamed AIMessageChunks without
        a subsequent complete TaskStatusUpdateEvent message.
    outbox — the last `a2a_outbox` a node wrote during this cycle, or None
        when no node wrote one. Taken from the cycle's own updates rather
        than from the graph state: the checkpoint keeps the channel's value
        across turns, and the next turn in the context would read it back.
    """

    delta_text: str
    outbox: Any = None


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
        self._outbox: Any = None

    @property
    def result(self) -> StreamResult:
        """Accumulated state. Valid after `execute()` iteration is complete."""
        return StreamResult(delta_text=self._delta_text, outbox=self._outbox)

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

            if event_type == "updates":
                self._track_outbox(event_data)

            if self._preprocessor:
                self._preprocessor.process(event_type, event_data)

            a2a_events = self._converter.convert(event_type, event_data)
            for a2a_event in a2a_events:
                self._track(a2a_event)
                yield a2a_event

    def _track_outbox(self, updates: Any) -> None:
        """Remember an `a2a_outbox` written by a node in this cycle.

        An "updates" event maps each node that just ran to what it wrote. A
        node writing None clears an outbox written earlier in the same cycle.
        """
        if not isinstance(updates, dict):
            return
        for written in updates.values():
            if isinstance(written, dict) and OUTBOX_KEY in written:
                self._outbox = written[OUTBOX_KEY]

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
