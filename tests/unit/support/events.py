"""Stand-ins for the a2a-sdk pieces an execution writes its events to."""

from __future__ import annotations

__all__ = ["RecordingQueue"]


class RecordingQueue:
    """An event queue that keeps what a client would have received.

    The real ``EventQueue`` only exposes ``enqueue_event``, so what reaches a
    client is observable exactly here and nowhere else.
    """

    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)
