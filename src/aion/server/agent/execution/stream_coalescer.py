"""Joins the chunks of the streamed answer that come faster than the stream needs them."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from typing import Optional

from a2a.types import Part, TaskArtifactUpdateEvent

from aion.core.a2a.deltas import is_stream_delta_chunk
from aion.core.a2a.enums import ArtifactId

__all__ = ["StreamDeltaCoalescer"]


class StreamDeltaCoalescer:
    """Holds back the chunks of ``aion:stream-delta`` that follow too closely on the last one sent.

    A model produces a chunk per token or two, and each chunk the server
    hands on is a write of the task and a push to every subscriber. The
    coalescer lets one chunk through per ``interval`` and joins the ones in
    between into it, text onto text. It is the same for every executor: it
    sits in the event pipeline, before the queue that feeds the stream, the
    task record and push alike.

    A chunk goes out at once when the interval since the last one has passed,
    and so does the first chunk of every section (``append`` unset), so a
    client sees each section begin without delay. Whatever is waiting goes
    out before any other event, so the order of the turn is kept; an event
    that starts the artifact over - a section's first chunk, a section's
    whole text - drops the chunks still waiting instead, since it replaces
    them. Nothing goes out on a timer: a chunk that waits is sent with the
    next event of the turn.

    Args:
        interval: Seconds between two chunks; 0 lets every chunk through.
        clock: Monotonic time, replaceable in tests.
    """

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval
        self._clock = clock
        self._waiting: Optional[TaskArtifactUpdateEvent] = None
        self._last_sent: Optional[float] = None

    def take(self, event: object) -> list[object]:
        """The events to deliver now that ``event`` has arrived, in order."""
        if is_stream_delta_chunk(event):
            self._hold(event)
            starts_section = not event.append
            if starts_section or self._last_sent is None or self._clock() - self._last_sent >= self._interval:
                return self.drain()
            return []
        if self._replaces_stream(event):
            self._waiting = None
            return [event]
        return [*self.drain(), event]

    def drain(self) -> list[object]:
        """The chunk waiting to go out, if any."""
        if self._waiting is None:
            return []
        waiting, self._waiting = self._waiting, None
        self._last_sent = self._clock()
        return [waiting]

    def _hold(self, chunk: TaskArtifactUpdateEvent) -> None:
        if self._waiting is None or not chunk.append:
            # A chunk without `append` starts its section over, so the chunks
            # before it would be replaced anyway.
            self._waiting = copy.deepcopy(chunk)
            return
        self._join(self._waiting, chunk)

    @staticmethod
    def _join(waiting: TaskArtifactUpdateEvent, chunk: TaskArtifactUpdateEvent) -> None:
        parts = waiting.artifact.parts
        for part in chunk.artifact.parts:
            if part.WhichOneof("content") == "text" and len(parts) and parts[-1].WhichOneof("content") == "text":
                parts[-1].text += part.text
            else:
                joined = Part()
                joined.CopyFrom(part)
                parts.append(joined)
        if chunk.artifact.HasField("metadata"):
            waiting.artifact.metadata.MergeFrom(chunk.artifact.metadata)

    @staticmethod
    def _replaces_stream(event: object) -> bool:
        return (
            isinstance(event, TaskArtifactUpdateEvent)
            and event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
            and not event.append
        )
