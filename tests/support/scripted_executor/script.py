"""What a scripted turn does: the A2A events it yields, a refusal, a crash, a wait for ``cancel()``.

A turn's script comes from the request when its message carries a
``ScriptPayload`` (how a scenario drives an executor through a real server),
and otherwise from the ``Script`` the executor was built with (how a unit
test drives it). A script names a refusal before the turn yields anything,
the outputs to yield, a crash in the middle of the turn, a lookup of an
artifact in the context, a turn that waits for ``cancel()`` - writing a
status update on a tick while it waits, if asked - and then reports what it
kept and ends without an outcome, as an executor the server stops does; or
ends ``COMPLETED``, as work delivered before the cancel; or never ends. A
turn can also report what the executor has been handed so far.

An output is an A2A event, anything else a unit test wants yielded, or its
JSON form, which a scenario sends:

* ``{"kind": "status", "state": "working", "text": ..., "data": {...},
  "metadata": {...}, "ephemeral": false}`` - a status update; ``state`` is
  the A2A state in lower case (``input-required``), ``text`` and ``data``
  make its message, ``metadata`` goes to the event;
* ``{"kind": "artifact", "id": ..., "text": ..., "append": false,
  "last_chunk": true, "metadata": {...}}`` - an artifact update;
* ``{"kind": "error", "code": ..., "message": ..., "retryable": false,
  "metadata": {...}}`` - a ``FAILED`` final status carrying the error.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional

from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf import json_format, struct_pb2
from pydantic import BaseModel, ConfigDict

from aion.core.a2a.errors import error_parts
from aion.core.a2a.metadata import mark_status_event_ephemeral
from aion.core.exceptions import ExtensionRejection

from .contract import SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA, URI, script_contract

_EVENT_EXTENSION_URI = "https://docs.aion.to/a2a/extensions/aion/event/1.0.0"

__all__ = [
    "COMPLETED",
    "Refusal",
    "Script",
    "ScriptPayload",
    "ScriptRunner",
    "event_of",
    "scripted_message",
]

COMPLETED = {"kind": "status", "state": "completed"}
"""The output that ends a turn as completed."""

_STATES = {
    "working": TaskState.TASK_STATE_WORKING,
    "completed": TaskState.TASK_STATE_COMPLETED,
    "failed": TaskState.TASK_STATE_FAILED,
    "canceled": TaskState.TASK_STATE_CANCELED,
    "rejected": TaskState.TASK_STATE_REJECTED,
    "input-required": TaskState.TASK_STATE_INPUT_REQUIRED,
    "auth-required": TaskState.TASK_STATE_AUTH_REQUIRED,
}


class Refusal(BaseModel):
    """An ``ExtensionRejection`` the turn raises before it yields anything."""

    code: str
    message: str
    retryable: bool = False


class ScriptPayload(BaseModel):
    """A script carried by the request, as JSON; the fields mean what they mean on ``Script``."""

    SCHEMA_URI: ClassVar[str] = SCRIPT_SCHEMA
    EVENT_TYPE: ClassVar[str] = SCRIPT_EVENT_TYPE

    model_config = ConfigDict(extra="forbid")

    outputs: list[dict[str, Any]] = []
    refuse: Optional[Refusal] = None
    crash_after: Optional[int] = None
    wait_for_cancel: bool = False
    hang_on_cancel: bool = False
    completes_on_cancel: bool = False
    tick_seconds: Optional[float] = None
    find_artifact: Optional[str] = None
    report: bool = False


@dataclass
class Script:
    """What one turn does.

    Attributes:
        outputs: What the turn yields, in order (see the module docstring).
        refuse: The turn refuses the request with this before it yields
            anything.
        crash_after: The turn raises a ``RuntimeError`` after yielding this
            many outputs.
        wait_for_cancel: After the outputs, the turn waits for ``cancel()``,
            then writes a status update with ``{"stopped_within": within}``
            under its URI - the ``within`` the server gave ``cancel()`` - and
            ends without an outcome: the server writes it.
        hang_on_cancel: With ``wait_for_cancel``, the turn does not end after
            ``cancel()`` either: it runs until the server tears it down.
        completes_on_cancel: With ``wait_for_cancel``, a ``cancel()`` ends the
            turn ``COMPLETED``, as work already delivered does.
        tick_seconds: With ``wait_for_cancel``, the turn writes a status
            update, ``{"ticks": n}`` under its URI, every this many seconds
            while it waits: a durable write, which is where a server learns
            that another one cancelled the task.
        find_artifact: Before the outputs, the turn looks this artifact id up
            in its context and says what it found in a message whose data part
            is ``{"found": false}`` or ``{"found": true, "task_id", "version",
            "text"}``.
        report: Before the outputs, the turn says what the server handed the
            executor in a message whose data part is ``{"kind", "task_id",
            "state", "cancels", ...}``: how this turn began (``start`` or
            ``resume``), its task, the object the task held, every
            ``cancel()`` the executor has been asked for as ``{"task_id",
            "within"}``, and what the reporting executor adds.
    """

    outputs: list[Any] = field(default_factory=lambda: [dict(COMPLETED)])
    refuse: Optional[Refusal] = None
    crash_after: Optional[int] = None
    wait_for_cancel: bool = False
    hang_on_cancel: bool = False
    completes_on_cancel: bool = False
    tick_seconds: Optional[float] = None
    find_artifact: Optional[str] = None
    report: bool = False

    @classmethod
    def from_payload(cls, payload: ScriptPayload) -> "Script":
        """The script a request carried."""
        return cls(
            outputs=list(payload.outputs),
            refuse=payload.refuse,
            crash_after=payload.crash_after,
            wait_for_cancel=payload.wait_for_cancel,
            hang_on_cancel=payload.hang_on_cancel,
            completes_on_cancel=payload.completes_on_cancel,
            tick_seconds=payload.tick_seconds,
            find_artifact=payload.find_artifact,
            report=payload.report,
        )


def _struct(value: Mapping[str, Any]) -> struct_pb2.Struct:
    struct = struct_pb2.Struct()
    json_format.ParseDict(dict(value), struct)
    return struct


def _message(task_id: str, context_id: str, parts: list[Part]) -> Message:
    return Message(
        message_id=uuid.uuid4().hex, task_id=task_id, context_id=context_id, role=Role.ROLE_AGENT, parts=parts
    )


def _parts(text: Optional[str], data: Optional[Mapping[str, Any]]) -> list[Part]:
    parts: list[Part] = []
    if text is not None:
        parts.append(Part(text=text))
    if data is not None:
        value = struct_pb2.Value()
        json_format.ParseDict(dict(data), value)
        parts.append(Part(data=value, media_type="application/json"))
    return parts


def event_of(output: Any, *, uri: str, task_id: str, context_id: str) -> Any:
    """The A2A event an output stands for; an output that is not a JSON form as it is."""
    if not isinstance(output, Mapping):
        return output
    kind = output["kind"]
    if kind == "artifact":
        artifact = Artifact(artifact_id=output["id"], parts=_parts(output.get("text", ""), output.get("data")))
        if output.get("metadata"):
            artifact.metadata.CopyFrom(_struct(output["metadata"]))
        return TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            artifact=artifact,
            append=bool(output.get("append", False)),
            last_chunk=bool(output.get("last_chunk", True)),
        )
    if kind == "error":
        message = _message(
            task_id,
            context_id,
            error_parts(uri, output["code"], output.get("message", ""), bool(output.get("retryable", False))),
        )
        status = TaskStatus(state=TaskState.TASK_STATE_FAILED, message=message)
    elif kind == "status":
        parts = _parts(output.get("text"), output.get("data"))
        state = _STATES[output.get("state", "working")]
        status = (
            TaskStatus(state=state, message=_message(task_id, context_id, parts)) if parts else TaskStatus(state=state)
        )
    else:
        raise ValueError(f"unknown output kind {kind!r}")
    event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=status)
    if output.get("metadata"):
        event.metadata.CopyFrom(_struct(output["metadata"]))
    if output.get("ephemeral"):
        mark_status_event_ephemeral(event)
    return event


def scripted_message(
        script: Mapping[str, Any],
        *,
        uri: str = URI,
        text: str = "go",
        message_id: str = "m-user",
        task_id: str = "",
        context_id: str = "",
) -> Message:
    """A user message that activates the extension at ``uri`` and carries ``script`` as its event's payload."""
    event_type, schema = script_contract(uri)
    message = Message(
        message_id=message_id,
        task_id=task_id,
        context_id=context_id,
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
        extensions=[uri],
        metadata=_struct({_EVENT_EXTENSION_URI: {"type": event_type, "source": "aion://tests", "id": "evt-1"}}),
    )
    data = struct_pb2.Value()
    json_format.ParseDict(dict(script), data)
    message.parts.append(Part(data=data, metadata=_struct({_EVENT_EXTENSION_URI: {"schema": schema}})))
    return message


ArtifactFinder = Callable[[str], Awaitable[Any]]


class ScriptRunner:
    """Runs the scripts of one executor and records the cancels it is asked for.

    Args:
        uri: The extension the events are written for.
    """

    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.cancels: list[tuple[str, Optional[float]]] = []
        self._cancelled: dict[str, asyncio.Event] = {}
        self._windows: dict[str, Optional[float]] = {}

    def cancel(self, task_id: str, within: Optional[float]) -> None:
        """Record a cancel and end the task's wait, if it waits."""
        self.cancels.append((task_id, within))
        self._windows.setdefault(task_id, within)
        self._cancel_event(task_id).set()

    async def run(
            self,
            kind: str,
            script: Script,
            *,
            task_id: str,
            context_id: str,
            state: Optional[Mapping[str, Any]] = None,
            find_artifact: Optional[ArtifactFinder] = None,
            report: Optional[Mapping[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """The turn the script describes.

        Raises:
            ExtensionRejection: The script refuses the request.
        """
        if script.refuse is not None:
            raise ExtensionRejection(script.refuse.code, script.refuse.message, retryable=script.refuse.retryable)
        if script.report:
            data = {
                "kind": kind,
                "task_id": task_id,
                "state": dict(state or {}),
                "cancels": [{"task_id": cancelled, "within": within} for cancelled, within in self.cancels],
                **(report or {}),
            }
            yield self._event({"kind": "status", "data": data}, task_id, context_id)
        if script.find_artifact is not None:
            yield await self._found(script.find_artifact, find_artifact, task_id, context_id)
        for index, output in enumerate(script.outputs):
            if script.crash_after is not None and index >= script.crash_after:
                raise RuntimeError("scripted crash")
            yield self._event(output, task_id, context_id)
        if script.crash_after is not None and script.crash_after >= len(script.outputs):
            raise RuntimeError("scripted crash")
        if script.wait_for_cancel:
            async for event in self._wait_for_cancel(script, task_id, context_id):
                yield event

    async def _wait_for_cancel(self, script: Script, task_id: str, context_id: str) -> AsyncIterator[Any]:
        cancelled = self._cancel_event(task_id)
        ticks = 0
        while not cancelled.is_set():
            if script.tick_seconds is None:
                await cancelled.wait()
                break
            try:
                await asyncio.wait_for(cancelled.wait(), script.tick_seconds)
            except TimeoutError:
                ticks += 1
                yield self._event({"kind": "status", "metadata": {self.uri: {"ticks": ticks}}}, task_id, context_id)
        if script.hang_on_cancel:
            await asyncio.Event().wait()
        if script.completes_on_cancel:
            yield self._event(dict(COMPLETED), task_id, context_id)
        else:
            yield self._event(
                {"kind": "status", "metadata": {self.uri: {"stopped_within": self._windows.get(task_id)}}},
                task_id,
                context_id,
            )

    async def _found(
            self, artifact_id: str, find: Optional[ArtifactFinder], task_id: str, context_id: str
    ) -> Any:
        found = await find(artifact_id) if find is not None else None
        data: dict[str, Any] = {"found": found is not None}
        if found is not None:
            data.update(
                task_id=found.task_id,
                version=found.version,
                text="".join(part.text for part in found.artifact.parts if part.HasField("text")),
            )
        return self._event({"kind": "status", "data": data}, task_id, context_id)

    def _event(self, output: Any, task_id: str, context_id: str) -> Any:
        return event_of(output, uri=self.uri, task_id=task_id, context_id=context_id)

    def _cancel_event(self, task_id: str) -> asyncio.Event:
        return self._cancelled.setdefault(task_id, asyncio.Event())
