"""The scripted executor: an ``ExtensionExecutor`` that yields what its script says and records what it was handed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, AsyncIterator, ClassVar, Optional

from a2a.server.agent_execution import RequestContext
from a2a.types import Task, TaskArtifactUpdateEvent
from google.protobuf import struct_pb2

from aion.core.a2a import codes
from aion.core.agent import conversation
from aion.core.exceptions import ConfigurationError, ExtensionRejection
from aion.core.runtime.context.extensions import ExtensionActivationError, MessagesCollector
from aion.core.utils.protobuf import proto_to_dict
from aion.server.agent.adapters import ExecutionConfig
from aion.server.agent.execution.extensions import ExecutableExtensionDescriptor, ExtensionExecutor

from .contract import URI
from .script import Script, ScriptPayload, ScriptRunner

__all__ = [
    "SCRIPTED",
    "ScriptedExecutor",
]


class ScriptedExecutor(ExtensionExecutor):
    """Yields its script and records what the server handed it, as an extension's executor does.

    It reads its script from the request, numbers the artifacts it writes
    and looks them up in the context with the turn's history
    (``aion.core.agent.conversation``); a turn run without one, outside the
    request executor, neither numbers nor finds.

    Args:
        options: The extension's options; the scripted executor takes none.
        script: The script of a turn whose request carries none.

    Class attributes a test may set on a subclass:
        unavailable: Why the executor cannot be created, if it cannot.
    """

    unavailable: ClassVar[Optional[str]] = None

    def __init__(
            self,
            options: Optional[Mapping[str, Any]] = None,
            *,
            script: Optional[Script] = None,
    ) -> None:
        super().__init__(options or {})
        if options:
            raise ConfigurationError(f"enabled_extensions gives {URI} options it does not take: {', '.join(options)}")
        if self.unavailable is not None:
            raise ConfigurationError(f"the extension {URI} cannot run on this server: {self.unavailable}")
        self.script = script or Script()
        self.runner = ScriptRunner(URI)
        self.turns: list[tuple[str, RequestContext, ExecutionConfig]] = []
        self._scripts = MessagesCollector(ScriptPayload, invalid_payload_code=codes.INVALID_PAYLOAD)

    @property
    def cancels(self) -> list[tuple[str, Optional[float]]]:
        """Every ``cancel()`` the executor was asked for, as ``(task_id, within)``."""
        return self.runner.cancels

    def stream(self, context: RequestContext, config: ExecutionConfig) -> AsyncIterator[Any]:
        return self._turn("start", context, config)

    def resume(self, context: RequestContext, config: ExecutionConfig) -> AsyncIterator[Any]:
        return self._turn("resume", context, config)

    async def cancel(self, config: ExecutionConfig, *, within: Optional[float] = None) -> None:
        self.runner.cancel(config.task_id, within)

    async def _turn(self, kind: str, context: RequestContext, config: ExecutionConfig) -> AsyncIterator[Any]:
        self.turns.append((kind, context, config))
        history = _history()
        events = self.runner.run(
            kind,
            self._script(context),
            task_id=config.task_id,
            context_id=config.context_id,
            state=_state_of(context.current_task),
            find_artifact=history.find_artifact if history is not None else None,
        )
        async for event in events:
            if history is not None and isinstance(event, TaskArtifactUpdateEvent):
                await history.stamp_artifact_version(event)
            yield event

    def _script(self, context: RequestContext) -> Script:
        try:
            event = self._scripts.collect(URI, context)
        except ExtensionActivationError as error:
            raise ExtensionRejection(codes.INVALID_PAYLOAD, error.reason or "The script is not valid.") from error
        if event is not None and isinstance(event.payload, ScriptPayload):
            return Script.from_payload(event.payload)
        return self.script


def _history() -> Optional[conversation.ConversationHistory]:
    """The turn's history; None for a turn run without one."""
    try:
        return conversation.history()
    except conversation.ConversationUnavailableError:
        return None


def _state_of(task: Optional[Task]) -> dict[str, Any]:
    """The executor's object under its URI in the task; empty when there is none."""
    if task is None or not task.HasField("metadata") or URI not in task.metadata:
        return {}
    value = task.metadata[URI]
    return proto_to_dict(value) if isinstance(value, struct_pb2.Struct) else {}


SCRIPTED = ExecutableExtensionDescriptor(
    uri=URI,
    description="Yields what its script says; the SDK's tests drive executable extensions with it.",
    active=False,
    executor=ScriptedExecutor,
)
"""The scripted executor's registration."""
