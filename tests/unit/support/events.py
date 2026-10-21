"""Stand-ins for the a2a-sdk pieces an execution writes its events to."""

from __future__ import annotations

__all__ = ["RecordingQueue"]


class RecordingQueue:
    """An event queue that keeps what a client would have received.

    The real ``EventQueue`` only exposes ``enqueue_event``, so what reaches a
    client is observable exactly here and nowhere else. ``closed`` is what
    ``is_closed`` answers: set it to stand for a queue that lost its consumer.
    """

    def __init__(self) -> None:
        self.events: list[object] = []
        self.closed = False

    async def enqueue_event(self, event) -> None:
        self.events.append(event)

    def is_closed(self) -> bool:
        return self.closed
