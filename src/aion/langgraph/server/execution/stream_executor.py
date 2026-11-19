"""Per-call stream executor for LangGraph astream."""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from .event_converter import LangGraphA2AConverter
from .event_preprocessor import LangGraphEventPreprocessor

logger = logging.getLogger(__name__)

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

STREAM_MODES = ["values", "messages", "custom", "updates"]

OUTBOX_KEY = "a2a_outbox"

STRUCTURED_RESPONSE_KEY = "structured_response"
"""Where ``langchain.agents.create_agent`` puts the answer its ``response_format`` asks for."""


@dataclass(frozen=True)
class StreamResult:
    """Accumulated state after one stream cycle.

    Both values are the last a node of the graph wrote during this cycle, or
    None when no node wrote one. They are taken from the cycle's own updates
    rather than from the graph state: the checkpoint keeps a channel's value
    across turns, and the next turn in the context would read it back.

    outbox — the `a2a_outbox` a node wrote.
    structured_response — the `structured_response` a node wrote: the
        object an agent built by `create_agent` with a `response_format`
        answers with, or the same key in a graph of the application's own.
    """

    outbox: Any = None
    structured_response: Any = None


class StreamExecutor:
    """Executes one astream cycle against a compiled LangGraph graph.

    Lifecycle: instantiate > iterate `execute()` > read `result`.
    Created fresh per stream/resume call. Does not know about response
    assembly or state persistence.

    The graph is streamed with its subgraphs: LangGraph reports what a node
    of a subgraph streams - the chunks of its model call, what it writes to
    the stream writer, which is how the Aion authoring calls deliver - under
    the subgraph's namespace, and leaves it out of a stream of the parent
    graph alone. Messages and custom events are converted whichever graph
    they come from. The outbox is the parent graph's: a subgraph's state
    reaches the parent as the update of the node that runs it, and so is
    the structured response.
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
        self._outbox: Any = None
        self._structured_response: Any = None

    @property
    def result(self) -> StreamResult:
        """Accumulated state. Valid after `execute()` iteration is complete."""
        return StreamResult(outbox=self._outbox, structured_response=self._structured_response)

    async def execute(
        self,
        inputs: Any,
        config: dict[str, Any],
        runtime_context: Optional[Any] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run astream and yield A2A events directly.

        Tracks the outbox and the structured response nodes write as events
        pass through.

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

        async for namespace, event_type, event_data in self._graph.astream(
            inputs, config, stream_mode=STREAM_MODES, subgraphs=True, **kwargs
        ):
            if event_type == "messages":
                event_data, _ = event_data

            # An empty namespace is the graph itself; a subgraph's names the
            # nodes that lead to it.
            if event_type == "updates" and not namespace:
                self._track_updates(event_data)

            if self._preprocessor:
                self._preprocessor.process(event_type, event_data)

            a2a_events = self._converter.convert(event_type, event_data)
            for a2a_event in a2a_events:
                yield a2a_event

    def _track_updates(self, updates: Any) -> None:
        """Remember an `a2a_outbox` or a `structured_response` written by a node in this cycle.

        An "updates" event maps each node that just ran to what it wrote. A
        node writing None clears a value written earlier in the same cycle.
        """
        if not isinstance(updates, dict):
            return
        for written in updates.values():
            if not isinstance(written, dict):
                continue
            if OUTBOX_KEY in written:
                self._outbox = written[OUTBOX_KEY]
            if STRUCTURED_RESPONSE_KEY in written:
                self._structured_response = written[STRUCTURED_RESPONSE_KEY]
