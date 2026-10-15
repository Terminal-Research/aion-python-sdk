"""The generic handler that serves one toolkit plugin.

``ToolkitTaskHandler`` is the ``ExtensionTaskHandler`` of every toolkit: one
instance per available toolkit, built by discovery. It does what the toolkit
protocol leaves to the server:

* **admission** - before a task exists, the caller check of an
  ``invocation_only`` toolkit, the one task per context of an
  ``exclusive_per_context`` toolkit - an abandoned task does not hold the
  context, it is settled - and the toolkit's ``preflight``; before a message
  continues a task, the same one task per context; a refusal is a JSON-RPC
  error whose ``data`` carries ``code`` and ``retryable``, and no task is
  created or changed;
* **turns** - ``start`` or ``resume`` with a ``ToolkitContext``, every value
  they yield translated by a ``TurnTranslator``; the turn is events, then
  exactly one result, and a turn that breaks that, yields something else or
  raises ends ``FAILED`` with ``internal_error``; the artifacts the toolkit
  writes are numbered per context and ``find_artifact`` reads them back
  (``TurnArtifacts``);
* **cancellation** - ``Toolkit.cancel`` and a bounded wait for the running
  turn to end with its own outcome; the executor writes ``CANCELED`` only
  for a turn that did not; a turn torn down before its outcome - its
  producer cancelled after another server cancelled the task - asks the
  toolkit to stop as well;
* **stopping** - at a server shutdown, ``Toolkit.cancel`` with ``SHUTDOWN``
  for every turn running here at once and one wait for them all, bounded by
  the window the server's shutdown budget gives the toolkits; when the run's
  event queue has lost its consumer, ``Toolkit.cancel`` once and nothing
  more written.

``Toolkit.cancel`` is told, as ``within``, how long the server waits for the
turn's result from then on: ``CANCEL_GRACE_SECONDS`` for a caller's cancel,
the shutdown window at a shutdown, and None when it waits for none.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from a2a.utils.errors import InternalError, InvalidParamsError
from google.protobuf import struct_pb2

from aion.core.exceptions import AionAuthenticationError, InvalidPrincipalError, ToolkitRejection
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.settings import api_settings
from aion.core.toolkit import (
    CANCEL,
    SHUTDOWN,
    ContextArtifact,
    ModelServiceAccess,
    PreflightContext,
    Toolkit,
    ToolkitArtifactEvent,
    ToolkitContext,
    ToolkitMessageEvent,
    ToolkitRequest,
    ToolkitResult,
    ToolkitStatusEvent,
    codes,
)
from aion.core.utils.protobuf import proto_to_dict
from aion.server.a2a.constants import ACTIVE_TASK_STATES, TERMINAL_TASK_STATES
from aion.server.auth import CredentialKind, verified_caller
from aion.server.tasks.abandoned import AbandonedTasks
from aion.server.tasks.admission import holder_of
from aion.server.tasks.context_artifacts import ContextArtifacts
from aion.server.tasks.context_runs import ContextRuns

from ...context.attribution import callback_attribution
from .artifacts import TurnArtifacts
from .secret_values import SecretMasker, deployment_secret_values
from .services import daemon_variables, model_service_access, toolkit_request
from .translation import ToolkitProtocolError, TurnTranslator

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events import EventQueue
    from aion.core.runtime.context.extensions import AionRuntimeExtensions

__all__ = [
    "CANCEL_GRACE_SECONDS",
    "PREFLIGHT_CRASH_MESSAGE",
    "ToolkitAdmission",
    "ToolkitTaskHandler",
    "context_busy",
    "refusal_data",
]

logger = logging.getLogger(__name__)

CANCEL_GRACE_SECONDS = 15.0
"""How long a caller's cancel waits for the running turn to end before the task is cancelled without it."""

PREFLIGHT_CRASH_MESSAGE = "The toolkit failed while checking the request; the server log has the details."
"""The JSON-RPC error message of a ``preflight`` that raised something other than a refusal."""

async def _no_artifact(_artifact_id: str) -> Optional[ContextArtifact]:
    return None


def refusal_data(code: str, retryable: Optional[bool] = None) -> dict[str, str]:
    """The ``data`` of a JSON-RPC error that refuses a request with ``code``.

    a2a-sdk sends ``data`` as the metadata of a ``google.rpc.ErrorInfo``,
    whose values are strings, so ``retryable`` travels as ``"true"`` or
    ``"false"``.

    Args:
        code: The refusal's code: a toolkit's own, or one of
            ``aion.core.toolkit.codes``.
        retryable: Whether repeating the request can succeed; by default the
            fixed answer for a code the server reserves.
    """
    if retryable is None:
        retryable = codes.RETRYABLE[code]
    return {"code": code, "retryable": "true" if retryable else "false"}


def context_busy(uri: str, context_id: str, task_id: str) -> InvalidParamsError:
    """The refusal of a request that would run a second task of ``uri`` in a context.

    ``data`` carries the code ``context_busy`` and, under ``taskId``, the
    unfinished task that holds the context: the caller can wait for it, or
    cancel it and send the request again.
    """
    return InvalidParamsError(
        message=(
            f"Context {context_id} already has an unfinished task of {uri} ({task_id}); "
            "wait for it to end or cancel it, then send the request again."
        ),
        data={**refusal_data(codes.CONTEXT_BUSY), "taskId": task_id},
    )


@dataclass
class _Turn:
    """A turn of a task running in this process.

    Attributes:
        finished: Set once the turn has ended, however it ended.
        stop_reason: The reason the toolkit was asked to stop the turn with,
            once it was asked; it is asked at most once per turn.
        outcome: The state of the final status the turn handed on, once it
            did; None for a turn that ended without one.
    """

    finished: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    stop_reason: Optional[str] = None
    outcome: Optional[int] = None


@dataclass(frozen=True)
class ToolkitAdmission:
    """What admission read off a request that starts a new task of a toolkit.

    Handed to the first turn, so the toolkit starts with the request
    ``preflight`` checked.

    Attributes:
        request: The message as the toolkit's payload models read it.
        daemon_variables: Configuration variables of the request's daemon
            environment.
        model_service: Model service access on the caller's behalf, or None.
    """

    request: ToolkitRequest
    daemon_variables: Mapping[str, str]
    model_service: Optional[ModelServiceAccess]


class ToolkitTaskHandler:
    """Serves the tasks of one toolkit: admission, turns and cancellation.

    Args:
        toolkit: The toolkit plugin.
        context_artifacts: Reads the versions of an artifact among the tasks
            of a context. With it, the artifacts a toolkit writes are numbered
            and ``ToolkitContext.find_artifact`` finds the latest version in the
            caller's context; without it they are not numbered and nothing is
            found.
        context_runs: Reads a context's generation and its unfinished task of
            this toolkit. With it, ``ToolkitContext.context_generation`` is
            filled and an ``exclusive_per_context`` toolkit runs one task per
            context; without it the generation is None and nothing is
            refused for a busy context.
        owner_resolver: Resolves a call's owner, as the task store does; it
            gives the context holder the caller was admitted as.
        secret_values: Returns the deployment's secret values; read at the
            start of every turn.
        abandoned_tasks: Settles an abandoned task. With it, an abandoned
            task of an ``exclusive_per_context`` toolkit no longer holds its
            context: the next request settles it and is admitted. Without it
            such a task holds the context until it is cancelled.
    """

    def __init__(
            self,
            toolkit: Toolkit,
            *,
            context_artifacts: Optional[ContextArtifacts] = None,
            context_runs: Optional[ContextRuns] = None,
            owner_resolver: OwnerResolver = resolve_user_scope,
            secret_values: Callable[[], Iterable[str]] = deployment_secret_values,
            abandoned_tasks: Optional[AbandonedTasks] = None,
    ) -> None:
        self.toolkit = toolkit
        self.descriptor = toolkit.descriptor
        self.uri = self.descriptor.uri
        self._context_artifacts = context_artifacts
        self._context_runs = context_runs
        self._abandoned_tasks = abandoned_tasks
        self._owner_resolver = owner_resolver
        self._secret_values = secret_values
        # The turn each task is running in this process.
        self._turns: dict[str, _Turn] = {}

    # -- admission ----------------------------------------------------------

    async def admit(
            self,
            message: Message,
            extensions: "AionRuntimeExtensions",
            call_context: "ServerCallContext",
    ) -> ToolkitAdmission:
        """Check a request that starts a new task of this toolkit, before the task exists.

        The caller check comes first, then the one task per context of an
        ``exclusive_per_context`` toolkit, then the toolkit's ``preflight``.

        Raises:
            InvalidParamsError: The caller may not start this toolkit
                (``caller_not_allowed``), the context already has an
                unfinished task of this toolkit (``context_busy``), or
                ``preflight`` refused the request with a ``ToolkitRejection``
                (its own code and ``retryable``).
            InternalError: ``preflight`` raised anything else
                (``internal_error``); the exception is logged.
        """
        self._check_caller(call_context)
        await self._check_context_free(message.context_id)
        runtime = _request_runtime(extensions, call_context)
        admission = ToolkitAdmission(
            request=toolkit_request(message, extensions, self.uri),
            daemon_variables=daemon_variables(extensions),
            model_service=model_service_access(runtime) if runtime is not None else None,
        )
        preflight_context = PreflightContext(
            context_id=message.context_id,
            daemon_variables=admission.daemon_variables,
            model_service=admission.model_service,
        )
        try:
            await self.toolkit.preflight(admission.request, preflight_context)
        except ToolkitRejection as rejection:
            logger.info("Toolkit %s refused a request [%s]: %s", self.uri, rejection.code, rejection.message)
            raise InvalidParamsError(
                message=rejection.message,
                data=refusal_data(rejection.code, rejection.retryable),
            ) from rejection
        except Exception:
            logger.exception("Toolkit %s failed while checking a request", self.uri)
            raise InternalError(
                message=PREFLIGHT_CRASH_MESSAGE,
                data=refusal_data(codes.INTERNAL_ERROR),
            ) from None
        return admission

    def _check_caller(self, call_context: "ServerCallContext") -> None:
        """Refuse a caller an ``invocation_only`` toolkit does not serve.

        On a server with ``AION_CLIENT_ID`` such a toolkit serves only calls
        the platform dispatched, with an invocation token. The rule follows
        the setting rather than a successful registration with the platform:
        a platform unreachable at startup must not open the toolkit to
        everyone. Without ``AION_CLIENT_ID`` - local development - there is
        no restriction.

        Raises:
            InvalidParamsError: ``caller_not_allowed``.
        """
        if not self.descriptor.invocation_only or not api_settings.client_id:
            return
        caller = verified_caller(call_context)
        if caller is not None and caller.credential is CredentialKind.INVOCATION:
            return
        logger.info("Refused a caller without an invocation token for toolkit %s", self.uri)
        raise InvalidParamsError(
            message=f"Only calls the Aion platform dispatches may start {self.uri} on this agent.",
            data=refusal_data(codes.CALLER_NOT_ALLOWED),
        )

    async def _check_context_free(self, context_id: str) -> None:
        """Refuse a new task of an ``exclusive_per_context`` toolkit in a context that has an unfinished one.

        The unfinished task is any owner's: a shared context's participants
        share the toolkit's state of the context too. The check reads the
        store, so it holds across the servers of the agent; nothing locks the
        context between this read and the new task's first write.

        An abandoned unfinished task - running, with no write for longer
        than ``AION_STALE_TASK_TIMEOUT``, its turn not in this process - is
        settled ``FAILED`` (``task_abandoned``) instead of holding the
        context, and the check reads again.

        Raises:
            InvalidParamsError: ``context_busy``, naming the unfinished task.
        """
        if not self.descriptor.exclusive_per_context or self._context_runs is None:
            return
        while (blocking := await self._context_runs.unfinished_task(context_id, self.uri)) is not None:
            if not await self._settled_as_abandoned(blocking):
                logger.info(
                    "Refused a second task of toolkit %s in context %s (task %s)", self.uri, context_id, blocking
                )
                raise context_busy(self.uri, context_id, blocking)
            logger.info(
                "The abandoned task %s no longer holds context %s for toolkit %s", blocking, context_id, self.uri
            )

    async def _settled_as_abandoned(self, task_id: str) -> bool:
        """Settle ``task_id`` if it is abandoned and its turn does not run here; whether it was."""
        if self._abandoned_tasks is None or task_id in self._turns:
            return False
        return await self._abandoned_tasks.settle(task_id) is not None

    def admit_continuation(self, task: Task) -> None:
        """Check a message that continues a task of this toolkit, before the task is changed.

        A task of an ``exclusive_per_context`` toolkit that is still running
        takes no further message: a2a-sdk would serve it as the next turn of
        the same task, and on another server alongside the running one. A
        task that waits for input (``INPUT_REQUIRED``, ``AUTH_REQUIRED``)
        continues as usual.

        Raises:
            InvalidParamsError: ``context_busy``, naming the task itself.
        """
        if self.descriptor.exclusive_per_context and task.status.state in ACTIVE_TASK_STATES:
            logger.info("Refused a message into the running task %s of toolkit %s", task.id, self.uri)
            raise context_busy(self.uri, task.context_id, task.id)

    # -- turns --------------------------------------------------------------

    async def stream(
            self,
            context: "RequestContext",
            admission: ToolkitAdmission,
            event_queue: Optional["EventQueue"] = None,
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the first turn of a new task with the admitted request.

        ``event_queue`` is the run's queue the executor writes what this
        yields to; while it has a consumer the turn goes on (see ``_turn``).
        """
        turn = self._turn(
            context,
            self.toolkit.start,
            admission.request,
            admission.daemon_variables,
            admission.model_service,
            task_metadata={},
            event_queue=event_queue,
        )
        async with contextlib.aclosing(turn):
            async for event in turn:
                yield event

    async def resume(
            self, context: "RequestContext", event_queue: Optional["EventQueue"] = None
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the next turn of a task, with the toolkit's object as the task holds it."""
        runtime = await AionRuntimeContextRegistry.aget_current_context() or AionRuntimeContext()
        task = context.current_task
        turn = self._turn(
            context,
            self.toolkit.resume,
            toolkit_request(context.message, runtime.extensions, self.uri),
            daemon_variables(runtime.extensions),
            model_service_access(runtime),
            task_metadata=_stored_object(task, self.uri),
            event_queue=event_queue,
        )
        async with contextlib.aclosing(turn):
            async for event in turn:
                yield event

    async def _turn(
            self,
            context: "RequestContext",
            run: Callable[[ToolkitRequest, ToolkitContext], AsyncIterator[Any]],
            request: ToolkitRequest,
            variables: Mapping[str, str],
            model_service: Optional[ModelServiceAccess],
            *,
            task_metadata: Mapping[str, Any],
            event_queue: Optional["EventQueue"] = None,
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Drive one turn of the toolkit and translate it; the turn closes on exactly one final status.

        Before each event goes out, the run's queue is checked: once it is
        closed while the turn goes on - its consumer failed, a write to the
        store among the causes - nothing the turn yields reaches the task or
        a client any more. The toolkit is then asked to stop (``CANCEL``),
        as for a cancel nobody is waiting for, and the turn ends without
        writing; the task keeps whatever a2a-sdk wrote for it.

        A turn torn down before it hands on its final status - its producer
        cancelled, its stream closed by the executor - asks the toolkit to
        stop (``CANCEL``) besides closing the toolkit's stream; a toolkit
        whose own await the cancellation reached has seen its stream end
        first. That is how a cancel made on another server reaches the
        toolkit: a2a-sdk notices it when this server's next write meets a
        version conflict, cancels the producer, and nothing is written into
        the task after its terminal state.
        """
        task = context.current_task
        masker = SecretMasker([*self._secret_values(), *deployment_secret_values(variables)])
        artifacts = self._turn_artifacts(context, task)
        toolkit_context = ToolkitContext(
            task_id=task.id,
            context_id=task.context_id,
            context_generation=(
                await self._context_runs.generation(task.context_id) if self._context_runs is not None else None
            ),
            task_metadata=task_metadata,
            daemon_variables=variables,
            model_service=_masking_tokens(model_service, masker),
            secret_values=masker.values,
            artifact_finder=artifacts.find if artifacts is not None else _no_artifact,
        )
        translator = TurnTranslator(
            uri=self.uri,
            task_id=task.id,
            context_id=task.context_id,
            turn_id=request.message_id,
            task_metadata=task_metadata,
            masker=masker,
            version_of=artifacts.number if artifacts is not None else None,
        )
        turn = _Turn()
        self._turns[task.id] = turn
        outputs: Any = None
        try:
            try:
                outputs = run(request, toolkit_context)
                result: Optional[ToolkitResult] = None
                async for value in outputs:
                    if result is not None:
                        raise ToolkitProtocolError("the toolkit yielded a value after its result")
                    if isinstance(value, ToolkitResult):
                        result = value
                    elif isinstance(value, (ToolkitMessageEvent, ToolkitArtifactEvent, ToolkitStatusEvent)):
                        for event in await translator.event(value):
                            if not await self._consumed(task.id, turn, event_queue):
                                return
                            yield event
                    else:
                        raise ToolkitProtocolError(
                            f"the toolkit yielded {type(value).__name__}, which is not a protocol value"
                        )
                if result is None:
                    raise ToolkitProtocolError("the toolkit's turn ended without a result")
                closing = await translator.result(result)
            except ToolkitProtocolError as error:
                logger.error("Toolkit %s broke the protocol in task %s: %s", self.uri, task.id, error)
                closing = translator.failure()
            except Exception:
                logger.exception("Toolkit %s failed in task %s", self.uri, task.id)
                closing = translator.failure()
            except (asyncio.CancelledError, GeneratorExit):
                await self._torn_down(task.id, turn)
                raise
            finally:
                await _close(outputs)
            for event in closing:
                if not await self._consumed(task.id, turn, event_queue):
                    return
                if event is closing[-1]:
                    turn.outcome = event.status.state
                yield event
        finally:
            if turn.outcome is None:
                await self._torn_down(task.id, turn)
            turn.finished.set()
            if self._turns.get(task.id) is turn:
                del self._turns[task.id]

    async def _torn_down(self, task_id: str, turn: _Turn) -> None:
        """Ask the toolkit to stop a turn that ends before its final status, unless it was asked already."""
        if turn.stop_reason is None:
            logger.info("The turn of task %s ended before its outcome; toolkit %s is asked to stop", task_id, self.uri)
        await self._stop(task_id, turn, CANCEL)

    async def _consumed(self, task_id: str, turn: _Turn, event_queue: Optional["EventQueue"]) -> bool:
        """Whether what the turn yields next still reaches the task; asks the toolkit to stop when it does not."""
        if event_queue is None or not event_queue.is_closed():
            return True
        logger.warning(
            "The events of task %s have no consumer any more; toolkit %s is asked to stop and "
            "the turn writes nothing more",
            task_id,
            self.uri,
        )
        await self._stop(task_id, turn, CANCEL)
        return False

    async def _stop(
            self, task_id: str, turn: Optional[_Turn], reason: str, within: Optional[float] = None
    ) -> None:
        """Ask the toolkit to stop ``task_id``'s turn, once per turn; a failure is logged.

        ``within`` is how long the server waits for the turn's result from
        now on; None when it waits for none.
        """
        if turn is not None:
            if turn.stop_reason is not None:
                return
            turn.stop_reason = reason
        try:
            await self.toolkit.cancel(task_id, reason, within=within)
        except Exception:
            logger.exception("Toolkit %s failed to stop task %s (%s)", self.uri, task_id, reason)

    def _turn_artifacts(self, context: "RequestContext", task: Task) -> Optional[TurnArtifacts]:
        """The turn's artifact versions, for the context holder the caller was admitted as."""
        if self._context_artifacts is None:
            return None
        return TurnArtifacts(
            self._context_artifacts,
            context_id=task.context_id,
            task_id=task.id,
            holder=holder_of(context.call_context, self._owner_resolver),
        )

    # -- cancellation -------------------------------------------------------

    async def cancel(self, context: "RequestContext", event_queue: "EventQueue") -> bool:
        """Ask the toolkit to stop the task, and give a turn running here a bounded time to end.

        Within ``CANCEL_GRACE_SECONDS`` - the ``within`` the toolkit is told
        - the turn may still yield artifacts and its result - ``canceled``,
        or ``completed`` for work it had already delivered - which reach the
        task through the run's queue like any turn's. The wait holds
        a2a-sdk's lock on the task, which is why it is short. For a task with
        no turn running here nothing is awaited, and the toolkit is told
        ``within=None``.

        Returns:
            True when the turn ended in time with a terminal status of its
            own: the task is closed and nothing more is to be written. False
            when no turn of the task runs here, the turn did not end in time,
            or it ended waiting for input: the executor writes ``CANCELED``.
        """
        task_id = context.task_id or (context.current_task.id if context.current_task else "")
        turn = self._turns.get(task_id)
        await self._stop(task_id, turn, CANCEL, CANCEL_GRACE_SECONDS if turn is not None else None)
        if turn is None:
            return False
        try:
            await asyncio.wait_for(turn.finished.wait(), CANCEL_GRACE_SECONDS)
        except TimeoutError:
            logger.warning(
                "Toolkit %s did not end the turn of task %s within %.0fs of the cancel; "
                "the task is cancelled without it and the toolkit's report goes to its log",
                self.uri,
                task_id,
                CANCEL_GRACE_SECONDS,
            )
            return False
        return turn.outcome in TERMINAL_TASK_STATES

    async def shutdown(self, within: float) -> None:
        """Stop every turn running here for a server shutdown, at once, and wait for them together.

        Each toolkit turn is asked to stop with ``SHUTDOWN`` and told
        ``within``; a toolkit closes the turn with a ``failed`` result coded
        ``server_shutdown``, which reaches the task and its clients through
        the run's queue like any result. The wait ends when every turn has
        ended, or after ``within`` seconds from the start; a turn still
        running then is left to the drain of the server's active tasks,
        which settles its task.

        Args:
            within: The toolkits' window of the server's shutdown budget
                (``aion.server.shutdown.ShutdownBudget.toolkit_turns``).
        """
        turns = dict(self._turns)
        if not turns:
            return
        logger.info("Stopping %d turn(s) of toolkit %s for the server shutdown", len(turns), self.uri)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        waiters = [asyncio.ensure_future(turn.finished.wait()) for turn in turns.values()]
        await asyncio.gather(*(self._stop(task_id, turn, SHUTDOWN, within) for task_id, turn in turns.items()))
        _done, pending = await asyncio.wait(waiters, timeout=max(0.0, deadline - loop.time()))
        for waiter in pending:
            waiter.cancel()
        if pending:
            logger.warning(
                "%d turn(s) of toolkit %s did not end within %.1fs of the shutdown",
                len(pending),
                self.uri,
                within,
            )


def _request_runtime(
        extensions: "AionRuntimeExtensions", call_context: "ServerCallContext"
) -> Optional[AionRuntimeContext]:
    """The runtime context the executor will build for this request, as far as callbacks need it.

    None when the request carries no callback attribution the server
    accepts; the executor refuses such a request later anyway.
    """
    try:
        attribution = callback_attribution(call_context, AionRuntimeContext(extensions=extensions))
    except (AionAuthenticationError, InvalidPrincipalError) as error:
        logger.debug("No callback attribution for this request: %s", error)
        return None
    return AionRuntimeContext(extensions=extensions, callback_attribution=attribution)


def _stored_object(task: Task, uri: str) -> dict[str, Any]:
    """The toolkit's object as the task holds it; empty when there is none."""
    if not task.HasField("metadata") or uri not in task.metadata:
        return {}
    value = task.metadata[uri]
    return proto_to_dict(value) if isinstance(value, struct_pb2.Struct) else {}


def _masking_tokens(
        access: Optional[ModelServiceAccess], masker: SecretMasker
) -> Optional[ModelServiceAccess]:
    """``access`` whose every minted token is masked in the turn from the moment it exists."""
    if access is None:
        return None
    mint = access.token

    async def token() -> str:
        value = await mint()
        masker.add(value)
        return value

    return dataclasses.replace(access, token=token)


async def _close(outputs: Any) -> None:
    """Close the toolkit's stream, so a turn torn down early stops the toolkit's work."""
    aclose = getattr(outputs, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        logger.exception("Closing a toolkit's stream failed")
