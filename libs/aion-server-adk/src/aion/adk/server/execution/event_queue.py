"""ADK invocation event queue.

Architectural split mirrors a2a-sdk's EventQueue / EventConsumer pattern:

  ADKEventQueue    — publisher side: enqueue_event, enqueue_error, close.
  ADKEventConsumer — subscriber side: consume_all, agent_task_callback.

Two sources write into the queue during a single invocation:
  - agent.run_async()  — via _run_agent in ADKStreamExecutor
  - thread.reply() etc — via the ContextVar emitter (queue.enqueue_event)

Events and errors travel through separate methods to keep the data channel
typed cleanly. Errors are wrapped in _ErrorItem so consume_all can
distinguish them from normal Events without isinstance(item, BaseException).
"""

from __future__ import annotations
import logging

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from google.adk.events import Event

logger = logging.getLogger(__name__)

_CLOSED = object()
"""Sentinel placed in the queue when it is closed."""


@dataclass(frozen=True)
class _ErrorItem:
    """Wrapper for agent exceptions forwarded through the data queue."""
    exc: BaseException


class ADKEventQueue:
    """Publisher side of the ADK invocation event bus.

    Separates the write interface (enqueue_event, enqueue_error) from the
    read interface (dequeue_event, task_done). Readable only through
    ADKEventConsumer.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._is_closed: bool = False

    def enqueue_event(self, event: Event) -> None:
        """Non-blocking enqueue for ADK Events.

        Safe to call from any async context. Used by the agent task
        (forwarding agent.run_async() events) and by the ContextVar emitter
        (thread.reply, thread.typing, etc.).
        """
        if self._is_closed:
            logger.warning("ADKEventQueue: queue is closed — event dropped.")
            return
        self._queue.put_nowait(event)

    def enqueue_error(self, exc: BaseException) -> None:
        """Forward an agent exception through the queue to the consumer.

        Wraps the exception in _ErrorItem so the data channel stays typed
        as Event and errors are never confused with normal events.
        """
        if self._is_closed:
            logger.warning("ADKEventQueue: queue is closed — error dropped: %s", exc)
            return
        self._queue.put_nowait(_ErrorItem(exc))

    async def dequeue_event(self) -> Any:
        """Blocking dequeue. Returns an Event, _ErrorItem, or _CLOSED."""
        return await self._queue.get()

    def task_done(self) -> None:
        """Signal that the last dequeued item has been processed."""
        self._queue.task_done()

    def close(self) -> None:
        """Signal that no more events will be enqueued.

        Puts the _CLOSED sentinel so the consumer exits cleanly.
        Idempotent — safe to call multiple times.
        """
        if self._is_closed:
            return
        self._is_closed = True
        self._queue.put_nowait(_CLOSED)

    def is_closed(self) -> bool:
        return self._is_closed


class ADKEventConsumer:
    """Subscriber side of the ADK invocation event bus.

    Reads from ADKEventQueue until the queue is closed. Agent exceptions
    forwarded via enqueue_error() are re-raised here so the caller receives
    them naturally without extra error-channel machinery.

    Usage::

        queue = ADKEventQueue()
        consumer = ADKEventConsumer(queue)

        agent_task = asyncio.create_task(run_agent(queue, ctx))
        agent_task.add_done_callback(consumer.agent_task_callback)

        async for event in consumer.consume_all():
            process(event)
    """

    def __init__(self, queue: ADKEventQueue) -> None:
        self._queue = queue

    def agent_task_callback(self, task: asyncio.Task) -> None:
        """Done callback for the agent asyncio.Task.

        Ensures the queue is closed when the task finishes so consume_all()
        can exit even if _run_agent failed to call close() explicitly.
        """
        if not self._queue.is_closed():
            self._queue.close()

    async def consume_all(self) -> AsyncGenerator[Event, None]:
        """Yield Events until the queue is closed.

        task_done() is called immediately after each dequeue so the queue's
        internal join() counter stays accurate regardless of item type.
        Unexpected item types are logged and skipped without breaking the loop.
        """
        while True:
            item = await self._queue.dequeue_event()
            self._queue.task_done()

            if item is _CLOSED:
                return

            if isinstance(item, _ErrorItem):
                raise item.exc

            if not isinstance(item, Event):
                logger.warning(
                    "ADKEventConsumer: unexpected item type %s — skipped",
                    type(item).__name__,
                )
                continue

            yield item


__all__ = ["ADKEventQueue", "ADKEventConsumer"]
