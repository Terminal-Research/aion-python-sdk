"""ExtensionTaskHandler driving aion-toolkit-behaviour-evolution-python.

A task routed to BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 drives a real toolkit
run: parse the directive off the request (directive.py), wire an
EvolutionWorker from env config (tools_factory.py, behind the lazy toolkit
import), then consume the worker's typed event stream — phase transitions,
branch resolution (fresh vs resumed), the executor's live feed, the captured
spec — mapping each onto A2A events (events.py). Every WORKING update carries
a machine-readable progress struct so consumers can track the stage and
post-process content per type.

Resumability lives in the target repo, not in this process: the toolkit pins
each evolution to a stable branch (`evolution/{contextId}` — the A2A context
id) whose spec + commits are the durable state. Re-driving the same context
id clones, finds the branch, and continues, so `resume()` is simply another
`stream()` — no run pointer to restore. Task.metadata carries only
informational state (progress tracking, audit); nothing this handler emits is
load-bearing for a later run.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from a2a.utils.errors import UnsupportedOperationError
from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1
from aion.core.runtime.context.registry import AionRuntimeContextRegistry

from ..availability import ExtensionAvailability
from . import events
from .directive import ParsedDirective, parse_directive
from .errors import EvolutionHandlerError, ExtensionSetupError

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.core.a2a.extensions.daemon import DaemonExtensionPayload
    from aion.core.config.models import AgentConfig
    from aion.toolkits.behaviour_evolution import EvolutionWorker

logger = logging.getLogger(__name__)

_TOOLKIT_MODULE = "aion.toolkits.behaviour_evolution"


def _toolkit_installed() -> bool:
    try:
        return importlib.util.find_spec(_TOOLKIT_MODULE) is not None
    except (ImportError, ValueError):
        return False


class EvolutionTaskHandler:
    """Routes evolution-extension tasks away from the framework adapter.

    Available only when the agent's config opts the extension in AND the
    optional toolkit is importable - otherwise the executor prunes this
    handler at startup and a request declaring the extension is already
    rejected upstream by the descriptor pipeline (active=False).
    """

    uri = BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

    def __init__(
        self,
        build_worker: Optional[
            Callable[[ParsedDirective, Optional["DaemonExtensionPayload"]], "EvolutionWorker"]
        ] = None,
    ) -> None:
        # build_worker is a DI seam for tests; None defers to the real
        # factory in tools_factory.py, imported lazily inside stream().
        self._build_worker = build_worker
        self._running: dict[str, "EvolutionWorker"] = {}

    async def availability(self, config: "AgentConfig") -> ExtensionAvailability:
        enabled = getattr(config, "enabled_extensions", None) or ()
        if self.uri not in enabled:
            return ExtensionAvailability.unavailable(
                "the behaviour-evolution extension is not listed in this "
                "agent's enabled_extensions"
            )
        if self._build_worker is None and not _toolkit_installed():
            return ExtensionAvailability.unavailable(
                "the behaviour-evolution extension is enabled for this agent, "
                "but the behaviour-evolution toolkit package is "
                "not installed on this deployment"
            )
        return ExtensionAvailability.ok()

    async def stream(
        self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        task = context.current_task
        try:
            runtime_context = await AionRuntimeContextRegistry.aget_current_context()
            parsed = parse_directive(context, runtime_context)
        except EvolutionHandlerError as ex:
            logger.warning("evolution task %s rejected: %s", task.id, ex)
            yield events.failed_event(task, error=str(ex))
            return
        async for event in self._drive(context, parsed):
            yield event

    async def resume(
        self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Resume an interrupted evolution.

        The durable state is in the target repo (stable branch + spec), so
        resuming IS running: resume delegates straight to stream(), which
        re-parses the directive off the request and re-drives from the
        branch. The request must carry the directive event, same as any new
        task; a bare resume with no directive fails explicitly through
        stream()'s own DirectiveError handling.
        """
        async for event in self.stream(context):
            yield event

    async def _drive(
        self, context: "RequestContext", parsed: ParsedDirective
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run one toolkit slice for `parsed` and map its stream onto A2A."""
        task = context.current_task
        try:
            worker = await self._make_worker(parsed)
        except EvolutionHandlerError as ex:
            logger.warning("evolution task %s rejected: %s", task.id, ex)
            yield events.failed_event(task, error=str(ex))
            return

        self._running[task.id] = worker
        run_stream = worker.stream()
        try:
            async for event in run_stream:
                mapped = events.map_stream_event(task, event)
                if mapped is not None:
                    yield mapped
        except Exception as ex:
            # Operational errors never escape the worker's stream (they land
            # in result.outcome/error); anything raised here is a wiring bug.
            logger.exception("evolution run for task %s crashed", task.id)
            yield events.failed_event(task, error=f"evolution run crashed: {ex}")
            return
        finally:
            self._running.pop(task.id, None)
            # If this generator was torn down mid-run (shutdown), closing the
            # worker stream cancels the underlying run - no orphans.
            await run_stream.aclose()

        # The worker exposes the terminal result once its stream has drained
        # (the RunCompleted event flows through map_stream_event as a no-op).
        # Reading it from `worker.result` — rather than intercepting the
        # RunCompleted event by class name — keeps the handler free of any type
        # coupling to the optional toolkit.
        result = worker.result
        if result is not None:
            for event in events.result_events(task, result):
                yield event

    async def cancel(self, context: "RequestContext") -> None:
        task = context.current_task
        worker = self._running.get(task.id) if task is not None else None
        if worker is None:
            # Nothing in flight in this process (already finished, or the
            # run never started); the executor still emits terminal CANCELED.
            raise UnsupportedOperationError()
        worker.cancel()

    async def _make_worker(self, parsed: ParsedDirective) -> "EvolutionWorker":
        # The daemon payload rides along: it names the model (environment's
        # `llm` configuration variable) and the principal that Codex usage is
        # attributed to (environment.daemon_agent_identity_id). A resumed run
        # may still carry no daemon scope — the factory then falls back to env
        # overrides and fails precisely when it cannot.
        runtime_context = await AionRuntimeContextRegistry.aget_current_context()
        daemon = runtime_context.get_daemon() if runtime_context is not None else None
        if self._build_worker is not None:
            return self._build_worker(parsed, daemon)
        try:
            from .tools_factory import build_worker
        except ModuleNotFoundError as ex:
            raise ExtensionSetupError(f"behaviour-evolution toolkit is not installed: {ex}") from ex
        return build_worker(parsed, daemon)
