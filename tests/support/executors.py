"""A framework executor a test scripts with callables, and the request executor built over it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from types import SimpleNamespace
from typing import Any, Optional

from a2a.server.owner_resolver import resolve_user_scope
from a2a.utils.errors import UnsupportedOperationError

from aion.server.agent.adapters import AgentFrameworkExecutor, ExecutionConfig
from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.extensions import ExtensionExecutor

__all__ = ["FakeFrameworkExecutor", "agent_of", "request_executor"]

Turn = Callable[[Any], AsyncIterator[Any]]


class FakeFrameworkExecutor(AgentFrameworkExecutor):
    """A framework executor whose turns are the callables a test gives it.

    Args:
        stream: Called with the request context for a new task; yields its
            events. None yields nothing.
        resume: The same for a continued task; ``stream`` when None.
        cancel: Called with ``(config, within)``; None makes the executor one
            that cannot stop a turn (``UnsupportedOperationError``).

    Every call is recorded: ``calls`` as ``(operation, context, config)``,
    ``cancels`` as ``(config, within)``.
    """

    framework = "fake"

    def __init__(
            self,
            stream: Optional[Turn] = None,
            resume: Optional[Turn] = None,
            cancel: Optional[Callable[[ExecutionConfig, Optional[float]], Awaitable[None]]] = None,
    ) -> None:
        self._stream = stream
        self._resume = resume or stream
        self._cancel = cancel
        self.calls: list[tuple[str, Any, ExecutionConfig]] = []
        self.cancels: list[tuple[ExecutionConfig, Optional[float]]] = []

    async def stream(self, context, config):
        self.calls.append(("stream", context, config))
        if self._stream is not None:
            async for event in self._stream(context):
                yield event

    async def resume(self, context, config):
        self.calls.append(("resume", context, config))
        if self._resume is not None:
            async for event in self._resume(context):
                yield event

    async def cancel(self, config: ExecutionConfig, *, within: Optional[float] = None) -> None:
        self.cancels.append((config, within))
        if self._cancel is None:
            raise UnsupportedOperationError()
        await self._cancel(config, within)


def agent_of(executor: Any, *, agent_id: str = "agent", owner_resolver=resolve_user_scope) -> SimpleNamespace:
    """A built agent, as the request executor reads one: its framework executor, id and owner resolver."""
    return SimpleNamespace(get_executor=lambda: executor, id=agent_id, owner_resolver=owner_resolver)


def request_executor(
        framework: Optional[Any] = None,
        *,
        extensions: Optional[Mapping[str, ExtensionExecutor]] = None,
        file_transformer: Any = None,
        **agent: Any,
) -> AionAgentRequestExecutor:
    """The request executor over ``framework`` (a ``FakeFrameworkExecutor`` by default) and ``extensions``."""
    return AionAgentRequestExecutor(
        agent_of(framework if framework is not None else FakeFrameworkExecutor(), **agent),
        file_transformer=file_transformer,
        extensions=extensions,
    )
