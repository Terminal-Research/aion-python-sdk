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

import asyncio
import importlib.util
import logging
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Optional, get_args

from a2a.types import Message, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from a2a.utils.errors import UnsupportedOperationError
from aion.core.a2a.extensions.behaviour_evolution import EVOLUTION_VIEW_MILESTONES
from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.a2a.utils import is_ephemeral_status_event

from ..availability import ExtensionAvailability
from ..errors import ExtensionPreflightError
from . import events
from .directive import ParsedDirective, parse_directive
from .errors import (
    INTERNAL_ERROR_CODE,
    EvolutionHandlerError,
    ExtensionSetupError,
)
if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.events import EventQueue
    from aion.core.a2a.extensions.daemon import DaemonExtensionPayload
    from aion.core.config.models import AgentConfig
    from aion.toolkits.behaviour_evolution import EvolutionResult, EvolutionWorker

logger = logging.getLogger(__name__)

_TOOLKIT_MODULE = "aion.toolkits.behaviour_evolution"

# How long cancel() waits for a cancelled run to finish unwinding before giving
# up on reporting its result. The wait exists because the toolkit's rescue -
# pushing whatever the run had already committed - happens during that unwind,
# and `rescue_pushed`/`rescue_bundle` are the caller's only word on whether the
# work survived. Bounded because cancel() is a request someone is waiting on:
# on timeout the terminal CANCELED still goes out, just without the result.
_CANCEL_DRAIN_TIMEOUT_S = 60.0


class _RunHandle:
    """A run in flight, and the means to wait for it to finish unwinding.

    `cancel()` and `_drive()` are separate coroutines - the cancel arrives on
    its own request while the run's generator is still being consumed by the
    executor. This is their rendezvous: `_drive` sets `finished` once the
    worker has fully unwound (rescue included) and parks the terminal result
    where `cancel()` can read it.
    """

    __slots__ = ("worker", "finished", "result")

    def __init__(self, worker: "EvolutionWorker") -> None:
        self.worker = worker
        self.finished = asyncio.Event()
        self.result: Optional["EvolutionResult"] = None


def _toolkit_installed() -> bool:
    try:
        return importlib.util.find_spec(_TOOLKIT_MODULE) is not None
    except (ImportError, ValueError):
        return False


def _toolkit_event_kinds() -> Optional[set[str]]:
    """Class names the installed toolkit's `EvolutionEvent` union carries.

    None when they cannot be read at all - no toolkit installed, or one whose
    package no longer exports the union. That is "nothing to compare against",
    which is not the same as "no drift" and must not be reported as such.

    This is the one function here that imports the toolkit eagerly rather than
    through `find_spec`. Reading a union's members requires the real classes,
    and the process is about to import them anyway on its first run.
    """
    try:
        from aion.toolkits.behaviour_evolution import EvolutionEvent
    except ImportError:
        return None
    members = get_args(EvolutionEvent)
    if not members:
        return None
    return {member.__name__ for member in members}


def _warn_on_event_kind_drift() -> None:
    """Check the installed toolkit's event union against the mapper's table.

    Called from `availability()` because that is the one moment both halves are
    known to be present, and it runs once per executor build - app startup, via
    `AionAgentRequestExecutor.create` - rather than once per run. Not
    deduplicated: a process that builds several executors reports the same
    drift once each, which is noise rather than a problem.

    The stream-side warning in `events.map_stream_event` can only fire for a
    kind that actually occurs, and only after a run is under way; this reports
    the whole mismatch up front.

    Names only - which is the shallower half of the contract. A toolkit that
    renames a *field* (`AgentMessage.text`) passes this check and still breaks
    the mapper, since every field is read duck-typed. Catching that needs the
    real classes driven against real events, which is the toolkit's own test
    suite, not this.

    Logged, never fatal. An unmapped event degrades to a dropped progress
    update - the run's result and terminal status come off `worker.result`, not
    off the stream - so refusing to serve the extension would cost the caller
    more than the drift being reported.
    """
    kinds = _toolkit_event_kinds()
    if kinds is None:
        return
    drift = events.event_kind_drift(kinds)
    if drift:
        logger.warning(
            "evolution: the installed toolkit and this server's event mapper "
            "disagree on event kinds %s - progress events of an unmapped kind "
            "will be dropped from the stream; the two are out of step",
            sorted(drift),
        )


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
        self._running: dict[str, _RunHandle] = {}

    async def availability(self, config: "AgentConfig") -> ExtensionAvailability:
        """Static capability check only — install-time facts that cannot change
        without a redeploy, never runtime env config.

        Deliberately excludes `resolve_provider()`/`CODEX_PROVIDER` and every
        other env var checked in tools_factory.py: this result is cached for
        the executor's whole process lifetime (see request_executor.create's
        mark_unavailable), but some deployments configure secrets onto an
        already-running pod after startup. Caching a runtime-config check
        here would wedge such a deployment as permanently "unavailable" until
        a restart, even after the operator fixes it. Runtime config is
        instead validated per request in build_worker(), which always sees
        the live environment — see ExtensionSetupError's raise sites.
        """
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
        _warn_on_event_kind_drift()
        return ExtensionAvailability.ok()

    async def preflight(self, context: "RequestContext") -> None:
        """Reject a request whose deployment environment cannot serve it,
        before its task is created - see ExtensionPreflightError.

        Runs only tools_factory.check_environment()'s checks - the subset of
        build_worker()'s that need just `daemon`, not a parsed directive.
        Directive validity itself is still discovered in stream(), which
        already reports it as a FAILED task naming the offending request
        field: that's the caller's request being wrong, not the deployment,
        so there's no reason to preflight it here.
        """
        if self._build_worker is not None:
            return  # DI seam for tests - no real toolkit env to check
        try:
            from .tools_factory import check_environment
        except ModuleNotFoundError:
            return  # availability() already rejects an enabled-but-missing toolkit
        runtime_context = await AionRuntimeContextRegistry.aget_current_context()
        daemon = runtime_context.get_daemon() if runtime_context is not None else None
        try:
            check_environment(daemon)
        except ExtensionSetupError as ex:
            logger.warning("evolution preflight rejected [%s]: %s", ex.code, ex)
            raise ExtensionPreflightError(ex.client_text) from ex

    async def stream(
        self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        task = context.current_task
        try:
            runtime_context = await AionRuntimeContextRegistry.aget_current_context()
            parsed = parse_directive(context, runtime_context)
        except EvolutionHandlerError as ex:
            # `str(ex)` to the log, `ex.client_text` over the wire: the two
            # differ wherever the message describes this deployment rather than
            # the request (see errors.py).
            logger.warning("evolution task %s rejected [%s]: %s", task.id, ex.code, ex)
            yield events.failed_event(task, error=ex.client_text, code=ex.code)
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
            logger.warning("evolution task %s rejected [%s]: %s", task.id, ex.code, ex)
            yield events.failed_event(task, error=ex.client_text, code=ex.code)
            return

        # One accumulator per run: every status event carries the whole
        # progress struct, so the last one persisted describes the entire run
        # rather than just its final moment (see events.RunProgress).
        progress = events.RunProgress(scope=parsed.scope)
        handle = _RunHandle(worker)
        self._running[task.id] = handle
        run_stream = worker.stream()
        # Under `milestones` the caller receives exactly what the task record
        # keeps. Deciding that by the ephemeral flag — the same mark the task
        # manager persists by — is what makes the promise hold: a future event
        # cannot be live-only for storage and durable for delivery, because
        # there is one mark, not two lists.
        milestones_only = parsed.view == EVOLUTION_VIEW_MILESTONES
        try:
            async for event in run_stream:
                mapped = events.map_stream_event(
                    task, event, progress=progress, view=parsed.view
                )
                if mapped is None:
                    continue
                if milestones_only and is_ephemeral_status_event(mapped):
                    continue
                yield mapped
        except Exception as ex:
            # Operational errors never escape the worker's stream (they land
            # in result.outcome/error); anything raised here is a wiring bug.
            # Its text is a traceback fragment, so the caller is told what the
            # run had reached rather than what threw — the accumulated progress
            # is the only usable fact here — and the detail stays in the log.
            logger.exception("evolution run for task %s crashed: %s", task.id, ex)
            yield events.failed_event(
                task,
                error=events.internal_error_text(progress.snapshot()),
                code=INTERNAL_ERROR_CODE,
                progress=progress.snapshot(),
            )
            return
        finally:
            self._running.pop(task.id, None)
            try:
                # If this generator was torn down mid-run (shutdown), closing
                # the worker stream cancels the underlying run - no orphans.
                await run_stream.aclose()
            finally:
                # Release cancel() whatever happened above: it is blocked on
                # this until its own timeout, and a failed close is no reason
                # to make a caller wait out that whole budget. `worker.result`
                # is None if the run never got that far, which cancel() reads
                # as "nothing to report".
                handle.result = worker.result
                handle.finished.set()

        # The worker exposes the terminal result once its stream has drained
        # (the RunCompleted event flows through map_stream_event as a no-op).
        # Reading it from `worker.result` — rather than intercepting the
        # RunCompleted event by class name — keeps the handler free of any type
        # coupling to the optional toolkit.
        result = worker.result
        if result is not None:
            for event in events.result_events(task, result, progress=progress):
                yield event

    async def cancel(
        self, context: "RequestContext", event_queue: "EventQueue"
    ) -> Optional[Message]:
        """Cancel the run and report what became of the work it had done.

        Returns the message the executor attaches to the terminal CANCELED, or
        None when there is nothing to report. Waiting for the unwind before
        returning is the point: the toolkit rescues undelivered commits during
        it, and anything published after the terminal CANCELED would be
        dropped by the event consumer, which shuts its queue down on any
        terminal state. A rescued bundle is published as its own artifact on
        `event_queue` here, before that terminal lands - the same artifact
        shape `result_events()` uses on the failed path, so a client sees one
        consistent thing regardless of how the run ended. The returned
        message carries only the closing text/data for the terminal itself.
        """
        task = context.current_task
        handle = self._running.get(task.id) if task is not None else None
        if handle is None:
            # Nothing in flight in this process (already finished, or the
            # run never started); the executor still emits terminal CANCELED.
            raise UnsupportedOperationError()
        handle.worker.cancel()
        try:
            await asyncio.wait_for(handle.finished.wait(), timeout=_CANCEL_DRAIN_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning(
                "evolution task %s did not finish unwinding within %ss; "
                "cancelling without a result report",
                task.id,
                _CANCEL_DRAIN_TIMEOUT_S,
            )
            return None

        result = handle.result
        if result is None or result.outcome != "cancelled":
            # The run reached its own terminal outcome inside the race window,
            # and _drive already reported it. Saying anything more here would
            # contradict a result the caller has in hand.
            return None
        bundle_event = events.rescue_bundle_artifact_event(task, result)
        if bundle_event is not None:
            await event_queue.enqueue_event(bundle_event)
        return events.cancel_result_message(task, result)

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
            raise ExtensionSetupError(
                f"behaviour-evolution toolkit is not installed: missing module {ex.name!r}"
            ) from ex
        return build_worker(parsed, daemon)
