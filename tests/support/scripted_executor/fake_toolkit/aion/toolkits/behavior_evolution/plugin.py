"""A stand-in for ``aion.toolkits.behavior_evolution.plugin``: the evolution toolkit as the SDK's wrapper calls it.

The SDK's wrapper (``aion.server.agent.execution.extensions.evolution``)
imports the toolkit's module lazily and calls only what is defined here. This
module answers every call the way the scripted executor does: the directive
carries a script (``tests.support.scripted_executor.script``), and a turn
yields its A2A events. A report adds what the wrapper handed the toolkit:
its provider, the model endpoint, whether it has a key, and how many secret
values it got.

``FAKE_EVOLUTION_UNAVAILABLE`` in the environment is what ``availability()``
answers. Every ``EvolutionToolkit`` the wrapper creates is kept in
``created``, so a unit test reads what it was handed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from aion.core.exceptions import ExtensionRejection
from tests.support.scripted_executor.contract import (
    EVOLUTION_DIRECTIVE_EVENT_TYPE,
    EVOLUTION_DIRECTIVE_SCHEMA,
    EVOLUTION_URI,
)
from tests.support.scripted_executor.script import Script, ScriptPayload, ScriptRunner

UNAVAILABLE_VARIABLE = "FAKE_EVOLUTION_UNAVAILABLE"
"""The environment variable whose value ``availability()`` answers."""


class EvolutionDirectiveEventPayload(ScriptPayload):
    """The directive, which carries the turn's script."""

    SCHEMA_URI: ClassVar[str] = EVOLUTION_DIRECTIVE_SCHEMA
    EVENT_TYPE: ClassVar[str] = EVOLUTION_DIRECTIVE_EVENT_TYPE


@dataclass(frozen=True)
class EvolutionRequest:
    message_id: str
    context_id: str
    text: str
    directive: Optional[EvolutionDirectiveEventPayload]


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    secret: Optional[Callable[[], Awaitable[str]]] = None
    attribution: Optional[tuple[str, str]] = None


async def _no_artifact(_artifact_id: str) -> Any:
    return None


@dataclass(frozen=True)
class EvolutionRun:
    task_id: str
    context_id: str
    turn_id: str
    context_generation: Optional[str] = None
    model: Optional[ModelEndpoint] = None
    secret_values: tuple[str, ...] = ()
    find_artifact: Callable[[str], Awaitable[Any]] = _no_artifact


def availability(*, provider: str) -> Optional[str]:
    """Why the toolkit cannot run here, from the environment; None when it can."""
    return os.environ.get(UNAVAILABLE_VARIABLE) or None


class EvolutionToolkit:
    """The scripted toolkit; ``created`` keeps every instance."""

    created: ClassVar[list["EvolutionToolkit"]] = []

    def __init__(self, *, provider: str) -> None:
        self.provider = provider
        self.runner = ScriptRunner(EVOLUTION_URI)
        self.preflights: list[EvolutionRequest] = []
        self.turns: list[tuple[str, EvolutionRequest, EvolutionRun]] = []
        self.created.append(self)

    async def preflight(self, request: EvolutionRequest) -> None:
        """Refuse the request as its script says, as the toolkit refuses a directive."""
        self.preflights.append(request)
        refusal = _script(request).refuse
        if refusal is not None:
            raise ExtensionRejection(refusal.code, refusal.message, retryable=refusal.retryable)

    def start(self, request: EvolutionRequest, run: EvolutionRun) -> AsyncIterator[Any]:
        return self._turn("start", request, run)

    def resume(self, request: EvolutionRequest, run: EvolutionRun) -> AsyncIterator[Any]:
        return self._turn("resume", request, run)

    async def cancel(self, task_id: str, *, within: Optional[float] = None) -> None:
        self.runner.cancel(task_id, within)

    async def _turn(self, kind: str, request: EvolutionRequest, run: EvolutionRun) -> AsyncIterator[Any]:
        self.turns.append((kind, request, run))
        model = None
        if run.model is not None:
            model = {
                "base_url": run.model.base_url,
                "attribution": list(run.model.attribution or ()) or None,
                "keyed": run.model.secret is not None,
            }
        events = self.runner.run(
            kind,
            _script(request),
            task_id=run.task_id,
            context_id=run.context_id,
            find_artifact=run.find_artifact,
            report={
                "provider": self.provider,
                "model": model,
                "context_generation": run.context_generation,
                "secret_values": len(run.secret_values),
            },
        )
        async for event in events:
            yield event


def _script(request: EvolutionRequest) -> Script:
    return Script.from_payload(request.directive) if request.directive is not None else Script()
