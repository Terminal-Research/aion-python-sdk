"""Handles execution result: produces terminal event and prepares task updates."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from google.protobuf import json_format
from pydantic_core import to_jsonable_python

from aion.core.a2a import A2AOutbox, data_artifact
from aion.core.exceptions import TaskFailedError
from aion.server.a2a.outbox import outbox_message, outbox_task
from .stream_executor import STRUCTURED_RESPONSE_KEY, StreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from langgraph.types import StateSnapshot

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Task | Message

logger = logging.getLogger(__name__)


class ExecutionResultHandler:
    """Processes execution result into terminal events and task side-effects.

    Applies the `a2a_outbox` a node wrote during this run through
    `aion.server.a2a.outbox`, which is where what the server does with an
    outbox is defined — for this adapter and the ADK one alike. The outbox
    comes from the run's own updates, not from the graph's final state: the
    checkpoint keeps the last value written in the context, and the next
    turn would otherwise answer with it again.

    A run that wrote no outbox but a `structured_response` - the object an
    agent built by `create_agent` with a `response_format` answers with -
    sends that object as an artifact of the task named `structured_response`,
    one data part holding it as JSON. The model's own text, which
    `ProviderStrategy` streams, stays the streamed answer beside it. An
    outbox is the run's answer when there is one, and the structured
    response is then not sent.

    Subclass and override `handle` to extend or replace the default logic.
    """

    def handle(
            self,
            stream_result: StreamResult,
            snapshot: "StateSnapshot",
            context: "RequestContext",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent]:
        """Produce A2A events based on execution result.

        Args:
            stream_result: Accumulated state from the stream cycle.
            snapshot: The graph's state after the run, LangGraph's own
                ``StateSnapshot`` from ``aget_state``. A non-empty
                ``interrupts`` means the graph stopped on ``interrupt()``
                and waits for input. Unused by the default logic and kept
                for subclasses that override this method.
            context: A2A request context (current_task, task_id, etc.).
            task_id: Current task ID (used to construct A2A events).
            context_id: Current context ID (used to construct A2A events).

        Returns:
            A2A events to emit before Complete/Interrupt.

        Raises:
            TaskFailedError: The structured response cannot be expressed as JSON.
        """
        outbox = stream_result.outbox
        structured_response = stream_result.structured_response
        if outbox is not None:
            result = self._handle_outbox(outbox, context, task_id, context_id)
            if result is not None:
                if structured_response is not None:
                    logger.info("The run wrote an outbox, which answers it; its structured response is not sent")
                return result
        if structured_response is not None:
            return [self._structured_response_event(structured_response, task_id, context_id)]
        return []

    @staticmethod
    def _structured_response_event(
            value: Any,
            task_id: str,
            context_id: str,
    ) -> TaskArtifactUpdateEvent:
        """The structured response as an artifact with one data part.

        The value is turned into JSON as Pydantic dumps it: a model, a
        dataclass, a dict and what they hold, an enum, a date. NaN and
        infinity become null, as in Pydantic's own JSON.
        """
        try:
            data = to_jsonable_python(value, inf_nan_mode="null")
            artifact = data_artifact(data, name=STRUCTURED_RESPONSE_KEY)
        except (ValueError, TypeError, json_format.Error) as e:
            raise TaskFailedError(
                f"The agent's structured response ({type(value).__name__}) cannot be sent as JSON."
            ) from e
        return TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            artifact=artifact,
            append=False,
            last_chunk=True,
        )

    def _handle_outbox(
            self,
            outbox: Any,
            context: "RequestContext | None",
            task_id: str,
            context_id: str,
    ) -> list[AgentEvent] | None:
        """Route outbox to the appropriate handler based on type.

        Returns None if the outbox type is not recognized.
        """
        if not isinstance(outbox, A2AOutbox):
            return None

        if outbox.message is not None:
            return self._handle_outbox_message(outbox.message, task_id, context_id)

        if outbox.task is not None:
            return self._handle_outbox_task(outbox.task, context, task_id, context_id)

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
