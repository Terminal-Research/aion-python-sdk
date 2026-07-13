"""ExtensionTaskHandler driving aion-toolkit-behaviour-evolution-python.

Second increment: a task routed to BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 now
drives a real toolkit run - parse the directive off the request (directive.py),
wire an EvolutionWorker from env config (tools_factory.py, behind the lazy
toolkit import), poll worker.snapshot() into WORKING status updates (the
toolkit has no progress callback, poll is its only progress surface), then
publish the result artifact and terminal status (events.py).

Runs are single-shot (clone -> apply -> verify -> commit -> push): the flow
never emits INPUT_REQUIRED, so resume() is unreachable in practice and kept
minimal. Run-pointer persistence in Task.metadata and true mid-flow resume
are deferred past the MVP - see Notes/WorkPlans/aion-evolution-handler-mvp.md.
"""

from __future__ import annotations

import asyncio
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
from .errors import EvolutionHandlerError, SetupError

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.core.a2a.extensions.daemon import DaemonExtensionPayload
    from aion.core.config.models import AgentConfig
    from aion.toolkits.behaviour_evolution import EvolutionWorker

logger = logging.getLogger(__name__)

_TOOLKIT_MODULE = "aion.toolkits.behaviour_evolution"
_POLL_INTERVAL_S = 1.0


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
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        # build_worker is a DI seam for tests; None defers to the real
        # factory in tools_factory.py, imported lazily inside stream().
        self._build_worker = build_worker
        self._poll_interval_s = poll_interval_s
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
            worker = await self._make_worker(context)
        except EvolutionHandlerError as ex:
            logger.warning("evolution task %s rejected: %s", task.id, ex)
            yield events.failed_event(task, error=str(ex))
            return

        run = asyncio.ensure_future(worker.run())
        self._running[task.id] = worker
        try:
            last_progress = None
            while True:
                snapshot = worker.snapshot()
                progress = (snapshot.phase, snapshot.detail)
                if progress != last_progress:
                    last_progress = progress
                    yield events.snapshot_event(task, snapshot)
                if run.done():
                    break
                await asyncio.wait({run}, timeout=self._poll_interval_s)

            # Operational errors never escape worker.run() (they land in
            # result.outcome/error); anything raised here is a wiring bug.
            result = None if run.cancelled() else run.result()
        except Exception as ex:
            logger.exception("evolution run for task %s crashed", task.id)
            yield events.failed_event(task, error=f"evolution run crashed: {ex}")
            return
        finally:
            self._running.pop(task.id, None)
            if not run.done():
                # The producer driving this generator was torn down mid-run
                # (shutdown); don't leave an orphaned toolkit run behind.
                run.cancel()

        if result is not None:
            for event in events.result_events(task, result):
                yield event

    async def resume(
        self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        # Unreachable in practice: stream() never emits INPUT_REQUIRED, so no
        # evolution task can be interrupted. Terminate explicitly rather than
        # pretending a resumed run completed.
        yield events.failed_event(
            context.current_task,
            error="evolution runs are single-shot; resuming is not supported yet",
        )

    async def cancel(self, context: "RequestContext") -> None:
        task = context.current_task
        worker = self._running.get(task.id) if task is not None else None
        if worker is None:
            # Nothing in flight in this process (already finished, or the
            # run never started); the executor still emits terminal CANCELED.
            raise UnsupportedOperationError()
        worker.cancel()

    async def _make_worker(self, context: "RequestContext") -> "EvolutionWorker":
        runtime_context = await AionRuntimeContextRegistry.aget_current_context()
        parsed = parse_directive(context, runtime_context)
        # The daemon payload rides along: it names the model (environment's
        # `llm` configuration variable) and the principal that Codex usage is
        # attributed to (environment.daemon_agent_identity_id).
        daemon = runtime_context.get_daemon()
        if self._build_worker is not None:
            return self._build_worker(parsed, daemon)
        try:
            from .tools_factory import build_worker
        except ModuleNotFoundError as ex:
            raise SetupError(f"behaviour-evolution toolkit is not installed: {ex}") from ex
        return build_worker(parsed, daemon)
