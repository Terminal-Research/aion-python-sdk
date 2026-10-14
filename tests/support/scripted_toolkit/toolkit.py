"""The scripted toolkit: a plugin that yields what it is told to.

A turn's script comes from the request when its message carries a
``ScriptPayload`` (how a scenario drives the toolkit through a real server),
and otherwise from the ``Script`` the toolkit was built with (how a unit test
drives it). A script names the protocol values to yield, a refusal or a crash
in ``preflight``, a crash in the middle of the turn, a lookup of an artifact
in the context, a turn that waits for ``cancel()`` and then ends
``canceled``, or a turn that reports what the toolkit has been handed so far.
The toolkit records every call, so a unit test can read what the server handed
it, and a scenario - whose server is another process - can read it from the
report.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, TypeAdapter

from aion.core.exceptions import ToolkitRejection
from aion.core.toolkit import (
    DataPart,
    Message,
    PreflightContext,
    ToolkitContext,
    ToolkitDescriptor,
    ToolkitMessageEvent,
    ToolkitOutput,
    ToolkitRequest,
    ToolkitResult,
    ToolkitState,
)

from .contract import (
    EXCLUSIVE_SCRIPT_EVENT_TYPE,
    EXCLUSIVE_SCRIPT_SCHEMA,
    EXCLUSIVE_URI,
    SCRIPT_EVENT_TYPE,
    SCRIPT_SCHEMA,
    URI,
)

__all__ = [
    "ExclusiveScriptPayload",
    "Refusal",
    "Script",
    "ScriptPayload",
    "ScriptedToolkit",
    "exclusive_toolkit",
    "toolkit",
]

_OUTPUTS = TypeAdapter(list[ToolkitOutput])


class Refusal(BaseModel):
    """A ``ToolkitRejection`` the script's ``preflight`` raises."""

    code: str
    message: str
    retryable: bool = False


class ScriptPayload(BaseModel):
    """A script carried by the request, as JSON.

    ``outputs`` are protocol values in their JSON form (``kind`` tells them
    apart); the other fields mean what they mean on ``Script``.
    """

    SCHEMA_URI: ClassVar[str] = SCRIPT_SCHEMA
    EVENT_TYPE: ClassVar[str] = SCRIPT_EVENT_TYPE

    model_config = ConfigDict(extra="forbid")

    outputs: list[dict[str, Any]] = []
    refuse: Optional[Refusal] = None
    crash_preflight: bool = False
    crash_after: Optional[int] = None
    wait_for_cancel: bool = False
    find_artifact: Optional[str] = None
    report: bool = False


class ExclusiveScriptPayload(ScriptPayload):
    """The script of a turn of the exclusive toolkit: the same fields, under its own event and schema."""

    SCHEMA_URI: ClassVar[str] = EXCLUSIVE_SCRIPT_SCHEMA
    EVENT_TYPE: ClassVar[str] = EXCLUSIVE_SCRIPT_EVENT_TYPE


@dataclass
class Script:
    """What one turn does.

    Attributes:
        outputs: What the turn yields, in order. Anything goes: a test of the
            host's checks yields values that are not protocol values.
        refuse: ``preflight`` refuses the request with this.
        crash_preflight: ``preflight`` raises a ``RuntimeError``.
        crash_after: The turn raises a ``RuntimeError`` after yielding this
            many outputs.
        wait_for_cancel: After the outputs, the turn waits for ``cancel()``
            and then ends with a ``canceled`` result.
        find_artifact: Before the outputs, the turn looks this artifact id up
            in its context and says what it found in a message whose data part
            is ``{"found": false}`` or ``{"found": true, "task_id", "version",
            "text"}``.
        report: Before the outputs, the turn says what the server handed the
            toolkit in a message whose data part is ``{"kind", "task_id",
            "task_metadata", "cancels"}``: how this turn began (``start`` or
            ``resume``), its task, the object the task held, and every
            ``cancel()`` the toolkit has been asked for as ``{"task_id",
            "reason"}``.
    """

    outputs: list[Any] = field(default_factory=lambda: [ToolkitResult(state=ToolkitState.COMPLETED)])
    refuse: Optional[Refusal] = None
    crash_preflight: bool = False
    crash_after: Optional[int] = None
    wait_for_cancel: bool = False
    find_artifact: Optional[str] = None
    report: bool = False

    @classmethod
    def from_payload(cls, payload: ScriptPayload) -> "Script":
        """The script a request carried."""
        return cls(
            outputs=list(_OUTPUTS.validate_python(payload.outputs)),
            refuse=payload.refuse,
            crash_preflight=payload.crash_preflight,
            crash_after=payload.crash_after,
            wait_for_cancel=payload.wait_for_cancel,
            find_artifact=payload.find_artifact,
            report=payload.report,
        )


class ScriptedToolkit:
    """A ``Toolkit`` that yields its script and records what the server handed it.

    Args:
        script: The script of a turn whose request carries none.
        descriptor: What the toolkit declares; by default the scripted
            toolkit's URI with ``ScriptPayload`` as its payload.
        configure_error: Raised by ``configure()``.
        unavailable: Returned by ``availability()``.
    """

    def __init__(
            self,
            script: Optional[Script] = None,
            *,
            descriptor: Optional[ToolkitDescriptor] = None,
            configure_error: Optional[Exception] = None,
            unavailable: Optional[str] = None,
    ) -> None:
        self.descriptor = descriptor or ToolkitDescriptor(
            uri=URI,
            description="Yields what its script says; the SDK's tests drive the toolkit host with it.",
            payloads=(ScriptPayload,),
        )
        self.script = script or Script()
        self._configure_error = configure_error
        self._unavailable = unavailable
        self.configured = 0
        self.preflights: list[tuple[ToolkitRequest, PreflightContext]] = []
        self.turns: list[tuple[str, ToolkitRequest, ToolkitContext]] = []
        self.cancels: list[tuple[str, str]] = []
        self._cancelled: dict[str, asyncio.Event] = {}

    def configure(self) -> None:
        self.configured += 1
        if self._configure_error is not None:
            raise self._configure_error

    async def availability(self, agent_config: Any) -> Optional[str]:
        return self._unavailable

    async def preflight(self, request: ToolkitRequest, context: PreflightContext) -> None:
        self.preflights.append((request, context))
        script = self._script(request)
        if script.crash_preflight:
            raise RuntimeError("scripted preflight crash")
        if script.refuse is not None:
            raise ToolkitRejection(
                script.refuse.code, script.refuse.message, retryable=script.refuse.retryable
            )

    def start(self, request: ToolkitRequest, context: ToolkitContext) -> AsyncIterator[Any]:
        return self._turn("start", request, context)

    def resume(self, request: ToolkitRequest, context: ToolkitContext) -> AsyncIterator[Any]:
        return self._turn("resume", request, context)

    async def cancel(self, task_id: str, reason: str) -> None:
        self.cancels.append((task_id, reason))
        self._cancel_event(task_id).set()

    async def _turn(self, kind: str, request: ToolkitRequest, context: ToolkitContext) -> AsyncIterator[Any]:
        self.turns.append((kind, request, context))
        script = self._script(request)
        if script.report:
            yield self._reported(kind, context)
        if script.find_artifact is not None:
            yield await self._found(script.find_artifact, context)
        for index, value in enumerate(script.outputs):
            if script.crash_after is not None and index >= script.crash_after:
                raise RuntimeError("scripted crash")
            yield value
        if script.crash_after is not None and script.crash_after >= len(script.outputs):
            raise RuntimeError("scripted crash")
        if script.wait_for_cancel:
            await self._cancel_event(context.task_id).wait()
            yield ToolkitResult(state=ToolkitState.CANCELED)

    def _reported(self, kind: str, context: ToolkitContext) -> ToolkitMessageEvent:
        data = {
            "kind": kind,
            "task_id": context.task_id,
            "task_metadata": dict(context.task_metadata),
            "cancels": [{"task_id": task_id, "reason": reason} for task_id, reason in self.cancels],
        }
        return ToolkitMessageEvent(message=Message(parts=[DataPart(data=data)]))

    @staticmethod
    async def _found(artifact_id: str, context: ToolkitContext) -> ToolkitMessageEvent:
        found = await context.find_artifact(artifact_id)
        data: dict[str, Any] = {"found": found is not None}
        if found is not None:
            data.update(
                task_id=found.task_id,
                version=found.version,
                text="".join(getattr(part, "text", "") for part in found.artifact.parts),
            )
        return ToolkitMessageEvent(message=Message(parts=[DataPart(data=data)]))

    def _script(self, request: ToolkitRequest) -> Script:
        if isinstance(request.payload, ScriptPayload):
            return Script.from_payload(request.payload)
        return self.script

    def _cancel_event(self, task_id: str) -> asyncio.Event:
        return self._cancelled.setdefault(task_id, asyncio.Event())


toolkit = ScriptedToolkit()
"""The object the ``scripted`` entry point names."""

exclusive_toolkit = ScriptedToolkit(
    descriptor=ToolkitDescriptor(
        uri=EXCLUSIVE_URI,
        description="Yields what its script says, one task per context at a time.",
        payloads=(ExclusiveScriptPayload,),
        exclusive_per_context=True,
    )
)
"""The object the ``scripted-exclusive`` entry point names: the same toolkit, ``exclusive_per_context``."""
