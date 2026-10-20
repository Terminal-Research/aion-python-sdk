"""Framework-agnostic A2A executor: runs every task with the executor the pool gives it."""
import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Tuple

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatusUpdateEvent
from a2a.helpers import new_task_from_user_message
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.telemetry import trace_function
from aion.core.a2a import codes
from aion.core.a2a.enums import A2AMetadataKey
from aion.core.a2a.errors import error_parts
from aion.core.exceptions import AionAuthenticationError, ExtensionRejection, InvalidPrincipalError
from aion.core.runtime import (
    AionRuntimeContextBuilder,
    ExtensionActivationError,
)
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.runtime.context.models import AionRuntimeContext
from aion.server.a2a.constants import ACTIVE_TASK_STATES, NON_ACTIVE_TASK_STATES, TERMINAL_TASK_STATES
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.a2a.utils import routed_extension_of
from aion.server.agent.adapters import AgentFrameworkExecutor, ExecutionConfig, StateScope, TaskExecutor
from aion.server.agent.aion_agent import AionAgent
from aion.server.agent.execution.scope import set_task_id
from aion.server.files.a2a import A2AFileTransformer

from .context.attribution import callback_attribution
from .event_pipeline import AionEventPipeline, OutboundFileNotStoredError
from .extensions import ExtensionExecutor
from .pool import EXTENSION_ROUTE_STATE_KEY, TaskExecutorPool, task_busy

if TYPE_CHECKING:
    from aion.core.runtime.context.extensions import AionRuntimeExtensions

logger = logging.getLogger(__name__)

# Task metadata key recording the extension a task is routed to. See
# docs/development/extension-exposure.md, "Task-routing".
_ROUTED_EXTENSION = A2AMetadataKey.ROUTED_EXTENSION.value

CANCEL_GRACE_SECONDS = 15.0
"""How long a caller's cancel waits for a running turn its executor stops to end, before the task is cancelled."""

CRASH_REASON = (
    "The agent stopped on an unexpected error. Its log has the details under task {task_id}."
)
"""What the task of a crashed turn says: that it crashed, and where to look.

The exception's own text stays in the log; it may name paths, hosts or
whatever the agent interpolated into it.
"""

SHUTDOWN_REASON = "The server stopped while the task was running; send the request again."
"""What the task of a turn a server shutdown stopped says."""

_CANCEL = "cancel"
_SHUTDOWN = "shutdown"


def public_reason_of(error: BaseException) -> Optional[str]:
    """The first reason a caller may read, along ``error``'s chain of causes.

    Frameworks and libraries wrap what an agent raises - an executor
    re-raises it as its own error, a model client wraps the SDK's refusal -
    so the error the server catches is often not the one that carries the
    reason. Causes are followed the way Python prints them: an explicit
    ``__cause__`` first, else the implicit ``__context__`` unless it was
    suppressed.

    Args:
        error: The exception a turn ended on.

    Returns:
        The first non-empty ``public_reason`` found, or None when nothing in
        the chain carries one.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        reason = getattr(current, "public_reason", None)
        if isinstance(reason, str) and reason:
            return reason
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return None


@dataclass
class _Turn:
    """A turn of a task running in this process.

    Attributes:
        executor: The executor running it.
        config: The execution it was started with.
        finished: Set once the turn has ended, however it ended.
        stopped_for: Why its executor was asked to stop it - ``_CANCEL`` or
            ``_SHUTDOWN`` - once it was; it is asked once.
        stoppable: Whether the executor took the request to stop it, so the
            server waits for the turn to end.
        terminal: Whether the task has its outcome: the turn's own, or the
            one the server wrote when the turn it stopped ended.
    """

    executor: TaskExecutor
    config: ExecutionConfig
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    stopped_for: Optional[str] = None
    stoppable: bool = False
    terminal: bool = False


class AionAgentRequestExecutor(AgentExecutor):
    """The A2A executor of an Aion agent: every task runs on the executor the pool gives it.

    The pool (``TaskExecutorPool``) holds the agent's framework executor and the
    executors of the executable extensions the agent enables. A new task
    goes to the extension its request activates, as the request handler
    decided before the task existed, and records it in its metadata; every
    later message and cancel of the task goes to the same executor. Every
    executor is driven the same way, and the turn around it is the server's:

    * a turn that raises fails its task. A turn that fails says why on its
      FAILED status, which the stream closes with, the unary call answers
      and a later `tasks/get` returns: a file that could not be stored and
      any error carrying a ``public_reason`` - the agent's own
      ``TaskFailedError``, or an SDK error naming what the deployment lacks
      - give their sentence, and the request ends normally. Any other error
      is a crash: it is logged with its traceback, the task names it as one
      and points at the log, and the request ends normally too. An executor
      that refuses its request (``ExtensionRejection``) closes the task
      ``REJECTED`` with the refusal. A task of an executable extension
      carries the code of its outcome in the extension's error shape
      (``aion.core.a2a.errors``);
    * a cancel asks the executor of a turn running here to stop it and waits
      up to ``CANCEL_GRACE_SECONDS`` for an executor that took the request;
      the stopped turn's task is closed ``CANCELED`` through the run's own
      queue, so a stream open on it closes with it - unless the turn closed
      the task with an outcome of its own first. Otherwise ``CANCELED`` is
      written into the cancel's queue;
    * a server shutdown asks every running turn to stop at once and waits
      for the ones whose executor took the request; a turn that ends without
      an outcome is closed ``FAILED`` coded ``server_shutdown``;
    * a turn whose events have lost their consumer - the run's queue closed
      while the turn goes on - is asked to stop, and nothing more of it is
      written; a turn torn down before its outcome - its producer cancelled
      after another server cancelled the task - is asked to stop as well.

    If an A2AFileTransformer is provided, inline (base64) file parts in
    everything the agent sends out - status and artifact updates, and the
    Message or Task an agent returns through its outbox - are stored and
    replaced with URL parts before being delivered. A file that could not be
    stored fails the task: the rest of its event still reaches the client and
    the run stops. The framework executor that produced the event is asked to
    forget it (``discard_undelivered``), so a copy the framework kept cannot
    be read back by a later turn. Inline bytes never reach the task record.
    """

    def __init__(
            self,
            aion_agent: AionAgent,
            file_transformer: Optional[A2AFileTransformer] = None,
            extensions: Optional[Mapping[str, ExtensionExecutor]] = None,
    ):
        """Build the executor over the agent's framework executor and the extensions' executors.

        Args:
            aion_agent: The built agent: its framework executor, its id and
                its owner resolver, which key the framework state of a turn.
            file_transformer: Stores the inline files the agent sends out.
            extensions: The executor of each executable extension the agent
                enables, by URI.
        """
        self.agent = aion_agent
        self.pool = TaskExecutorPool(default=aion_agent.get_executor(), extensions=dict(extensions or {}))
        self._file_transformer = file_transformer
        # The turn each task is running in this process.
        self._turns: dict[str, _Turn] = {}

    def route(self, extensions: "AionRuntimeExtensions") -> Optional[str]:
        """The executable extension a new task with these extensions goes to, or None for the framework executor."""
        return self.pool.route(extensions)

    def admit_continuation(self, task: Task) -> None:
        """Admit a message that continues ``task``, before the task is changed.

        A task is served by the executor it was routed to and by nothing
        else: on a server without that executor - an extension another image
        has - the message is refused with ``extension_unavailable``. While
        the task's turn runs, its executor decides
        (``TaskExecutor.message_to_busy_task``): the message is admitted as
        the task's next turn, or refused with ``task_busy``. A task
        waiting for input is admitted.

        Raises:
            InvalidParamsError: ``extension_unavailable`` or ``task_busy``;
                the task is left as it is.
        """
        uri = routed_extension_of(task)
        try:
            executor = self.pool.get(uri)
        except InvalidParamsError:
            logger.info("Refused a message into task %s: extension %s is not served here", task.id, uri)
            raise
        if task.status.state in ACTIVE_TASK_STATES and executor.message_to_busy_task == "refuse":
            logger.info("Refused a message into the running task %s", task.id)
            raise task_busy(task.context_id, task.id)

    async def stop_turns(self, within: float) -> None:
        """Stop the turns running here, at the start of a server shutdown.

        a2a-sdk's drain cancels an execution without asking it to stop, so
        every running turn's executor is asked first, all at once, and the
        turns whose executor took the request are awaited together, for at
        most ``within`` seconds. A turn that ends without an outcome is
        closed ``FAILED`` coded ``server_shutdown``; one still running then is
        left to the drain.

        Args:
            within: The window the shutdown budget gives the turns, in seconds.
        """
        turns = list(self._turns.values())
        if not turns:
            return
        logger.info("Stopping %d running turn(s) for the server shutdown", len(turns))
        stopped = await asyncio.gather(*(self._stop(turn, within, _SHUTDOWN) for turn in turns))
        waiters = [asyncio.ensure_future(turn.finished.wait()) for turn, took in zip(turns, stopped) if took]
        if not waiters:
            return
        _done, pending = await asyncio.wait(waiters, timeout=within)
        for waiter in pending:
            waiter.cancel()
        if pending:
            logger.warning("%d turn(s) did not end within %.1fs of the shutdown", len(pending), within)

    async def _stop(self, turn: _Turn, within: Optional[float], reason: str = _CANCEL) -> bool:
        """Ask the turn's executor to stop it, once; whether the executor took the request.

        An executor that cannot stop a turn raises ``UnsupportedOperationError``;
        one that fails is logged. Neither is waited for.
        """
        if turn.stopped_for is not None:
            return turn.stoppable
        turn.stopped_for = reason
        try:
            await turn.executor.cancel(turn.config, within=within)
        except UnsupportedOperationError:
            logger.debug("The executor of task %s cannot stop its turn", turn.config.task_id)
            return False
        except Exception:
            logger.exception("Asking the executor of task %s to stop its turn failed", turn.config.task_id)
            return False
        turn.stoppable = True
        return True

    @trace_function
    async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
    ) -> None:
        """Consume the entire execution stream within its request-local scope."""
        previous = await AionRuntimeContextRegistry.aget_current_context()
        try:
            await self._execute(context, event_queue)
        finally:
            await AionRuntimeContextRegistry.aset_current_context(previous)

    async def _execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Run one turn; execute() owns restoration on every exit path."""
        error = self._validate_request(context)
        if error:
            raise InvalidParamsError()

        execution = await self._get_task_for_execution(context)
        if execution is None:
            return

        task, is_new_task = execution
        # Local, not instance state: one executor instance serves every
        # concurrent request, so an attribute here would be overwritten by
        # whichever execution started most recently.
        task_updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            response_parameters = await self._setup_runtime_context(context)
        except (ExtensionActivationError, AionAuthenticationError, InvalidPrincipalError) as ex:
            raise InvalidParamsError(message=str(ex)) from ex

        # A new task takes the route the request handler admitted before the
        # task existed and records it - only the server decides which executor
        # a task belongs to; a continued task recalls it.
        uri = self._admitted_route(context) if is_new_task else routed_extension_of(task)
        executor = self.pool.get(uri)
        if is_new_task and uri is not None:
            task.metadata[_ROUTED_EXTENSION] = uri
        config = self._config(context, task)

        if is_new_task:
            logger.info("Created task")
            await event_queue.enqueue_event(task)
        else:
            logger.info("Resuming task")
            # A resumed task announces nothing of its own, so the turn is
            # acknowledged here instead of on the agent's first event: the
            # client learns the task is working without waiting out the model.
            await task_updater.start_work()

        pipeline = AionEventPipeline(
            event_queue,
            task_updater,
            self._file_transformer,
            task_started=not is_new_task,
            response_parameters=response_parameters,
            prior_message_ids=frozenset(
                [message.message_id for message in task.history]
                + ([task.status.message.message_id] if task.status.HasField('message') else [])
            ),
        )
        turn = _Turn(executor, config)
        self._turns[task.id] = turn
        try:
            await self._run_turn(turn, task, context, event_queue, task_updater, pipeline, is_new_task)
        finally:
            turn.finished.set()
            if self._turns.get(task.id) is turn:
                del self._turns[task.id]

    async def _run_turn(
            self,
            turn: _Turn,
            task: Task,
            context: RequestContext,
            event_queue: EventQueue,
            task_updater: TaskUpdater,
            pipeline: AionEventPipeline,
            is_new_task: bool,
    ) -> None:
        """Hand the turn's events on, and close its task when the turn breaks, is refused or is stopped."""
        executor, config = turn.executor, turn.config
        events: Any = None
        outcome: Optional[int] = None
        try:
            events = executor.stream(context, config) if is_new_task else executor.resume(context, config)
            async for agent_event in events:
                if event_queue.is_closed() is True:
                    logger.warning(
                        "The events of task %s have no consumer any more; its executor is asked to stop "
                        "and the turn writes nothing more",
                        task.id,
                    )
                    await self._stop(turn, None)
                    return
                if isinstance(agent_event, TaskStatusUpdateEvent):
                    outcome = agent_event.status.state
                await pipeline.process(agent_event)

        except OutboundFileNotStoredError as ex:
            # Each file's reason is already logged, one WARNING line per file.
            await self._discard_undelivered(turn, ex.event)
            await self._close_task(task_updater, pipeline, task, reason=ex.public_reason)
        except ExtensionRejection as rejection:
            logger.info("Task %s was refused [%s]: %s", task.id, rejection.code, rejection.message)
            await self._close_task(
                task_updater,
                pipeline,
                task,
                reason=rejection.message,
                code=rejection.code,
                retryable=rejection.retryable,
                state=TaskState.TASK_STATE_REJECTED,
            )
        except asyncio.CancelledError:
            if outcome not in NON_ACTIVE_TASK_STATES:
                # Torn down before its outcome: another server cancelled the
                # task, or the server is draining. The executor stops too.
                logger.info("The turn of task %s ended before its outcome; its executor is asked to stop", task.id)
                await self._stop(turn, None)
            raise
        except Exception as ex:
            reason = public_reason_of(ex)
            if reason is not None:
                # An outcome its raiser put in words for the caller - the
                # agent itself, or the SDK naming what a deployment lacks -
                # not a crash: the task fails saying so, and the request ends
                # normally. Raising would have a2a-sdk log it as an unexpected
                # error, twice and with a traceback.
                logger.warning("Task failed: %s", ex)
                await self._close_task(task_updater, pipeline, task, reason=reason)
                return
            # A crash: logged here with its traceback, and the task fails
            # saying it crashed. The request ends normally, so the FAILED with
            # that sentence is the task's only outcome: a raised error would
            # have a2a-sdk write a bare FAILED of its own, directly to the
            # store and ahead of the queued one.
            logger.exception("Task crashed: %s", ex)
            await self._close_task(task_updater, pipeline, task, reason=CRASH_REASON.format(task_id=task.id))
        else:
            if turn.stoppable and outcome not in NON_ACTIVE_TASK_STATES:
                await self._close_stopped(turn, task, task_updater, pipeline)
        finally:
            turn.terminal = turn.terminal or pipeline.terminal_seen
            await _close(events)

    async def _close_stopped(
            self, turn: _Turn, task: Task, task_updater: TaskUpdater, pipeline: AionEventPipeline
    ) -> None:
        """Close the task of a turn the server stopped, through the run's own queue: ``CANCELED`` or ``server_shutdown``."""
        if turn.stopped_for == _SHUTDOWN:
            await self._close_task(task_updater, pipeline, task, reason=SHUTDOWN_REASON, code=codes.SERVER_SHUTDOWN)
        elif not pipeline.terminal_seen:
            try:
                await task_updater.cancel()
            except Exception:  # noqa: BLE001 - the cancel writes CANCELED itself then
                logger.exception("Could not close the stopped turn of task %s as cancelled", task.id)
                return
        turn.terminal = True

    async def _discard_undelivered(self, turn: _Turn, event: Event) -> None:
        """Let the framework executor that produced an event that never reached the task forget it.

        Runs before the task is closed, so a turn that follows the FAILED
        state cannot read back what the client never received. An extension's
        executor keeps no copy of its own. Failures are logged and swallowed:
        the task fails either way.
        """
        if not isinstance(turn.executor, AgentFrameworkExecutor):
            return
        try:
            await turn.executor.discard_undelivered(turn.config, event)
        except Exception:  # noqa: BLE001 - must not mask the original failure
            logger.exception("Could not discard an event that was not delivered")

    @staticmethod
    async def _close_task(
            task_updater: TaskUpdater,
            pipeline: AionEventPipeline,
            task: Task,
            reason: Optional[str] = None,
            *,
            code: str = codes.INTERNAL_ERROR,
            retryable: Optional[bool] = None,
            state: int = TaskState.TASK_STATE_FAILED,
    ) -> None:
        """Put the task of a turn that broke, was refused or was stopped into its final state.

        The JSON-RPC error alone only reaches the caller of this one request:
        the task itself would stay WORKING in the store forever, so a later
        `tasks/get` — or any client that reconnects — sees work that is
        permanently in progress. Skipped when the producer already emitted a
        terminal state, since that outcome is the authoritative one.

        ``reason`` becomes the status's message, so the stream, the unary
        answer and a later `tasks/get` all say why. It is always a sentence
        written for the caller - a public reason, a refusal, or the crash
        notice - never an exception's own text. A task of an executable
        extension carries it in the extension's error shape, with ``code``
        and whether repeating can succeed. Failures here are swallowed so
        they cannot mask the original error.

        Args:
            task_updater: Updater bound to the turn's task.
            pipeline: Pipeline that processed the turn's events, consulted
                for whether a terminal state was already reported.
            task: The turn's task.
            reason: Sentence the caller reads, or None for a bare status.
            code: The outcome's code, for a task of an executable extension.
            retryable: Whether repeating can succeed; by default the fixed
                answer for ``code``.
            state: ``FAILED`` or ``REJECTED``.
        """
        if pipeline.terminal_seen:
            return
        uri = routed_extension_of(task)
        if uri is not None and reason:
            if retryable is None:
                retryable = codes.RETRYABLE.get(code, False)
            parts = error_parts(uri, code, reason, retryable)
        else:
            parts = [Part(text=reason)] if reason else []
        message = task_updater.new_agent_message(parts) if parts else None
        try:
            if state == TaskState.TASK_STATE_REJECTED:
                await task_updater.reject(message=message)
            else:
                await task_updater.failed(message=message)
        except Exception:  # noqa: BLE001 - must not mask the original failure
            logger.exception("Could not close the task after its turn ended without an outcome")

    async def cancel(
            self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Cancel with the current request's caller attribution, restoring any parent scope.

        A cancel activates none of the request's extensions: it carries no
        message for their payloads, and an extension declared in its
        ``A2A-Extensions`` header - a daemon, say - must not fail it, which
        a2a-sdk would record as a ``FAILED`` task.
        """
        previous = await AionRuntimeContextRegistry.aget_current_context()
        try:
            await AionRuntimeContextRegistry.aset_current_context(self._cancel_runtime_context(context))
            await self._cancel(context, event_queue)
        finally:
            await AionRuntimeContextRegistry.aset_current_context(previous)

    @staticmethod
    def _cancel_runtime_context(context: RequestContext) -> AionRuntimeContext:
        """The runtime context a cancel runs in: the caller's attribution when it has one, no extensions."""
        runtime_context = AionRuntimeContext()
        try:
            attribution = callback_attribution(context.call_context, runtime_context)
        except (AionAuthenticationError, InvalidPrincipalError) as error:
            logger.debug("A cancel without callback attribution: %s", error)
            return runtime_context
        return AionRuntimeContext(callback_attribution=attribution)

    async def _cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Request cancellation of an ongoing task.

        The task's executor is asked to stop its turn. A turn running here
        whose executor took the request is given ``CANCEL_GRACE_SECONDS`` to
        end - what it yields meanwhile reaches the task through the run's
        queue, and so does the ``CANCELED`` the run closes it with, or the
        outcome the turn wrote itself; nothing more is written then.
        Otherwise ``CANCELED`` is written into this request's queue, where a
        stream open on the task receives it. A task of an extension this
        server does not serve is cancelled all the same.

        Args:
            context: A2A request context with task to cancel
            event_queue: Queue for publishing cancellation events

        Raises:
            TaskNotFoundError: if context carries no task
            TaskNotCancelableError: if the task is already in a terminal state
        """
        task = context.current_task
        if task is None:
            raise TaskNotFoundError()

        if task.status.state in TERMINAL_TASK_STATES:
            raise TaskNotCancelableError(
                message=f"Task {task.id} cannot be canceled - current state: {task.status.state}"
            )

        task_updater = TaskUpdater(event_queue, task.id, task.context_id)
        turn = self._turns.get(task.id)
        if turn is not None:
            if await self._stop(turn, CANCEL_GRACE_SECONDS, _CANCEL):
                try:
                    await asyncio.wait_for(turn.finished.wait(), CANCEL_GRACE_SECONDS)
                except TimeoutError:
                    logger.warning(
                        "The turn of task %s did not end within %.0fs of the cancel; the task is cancelled without it",
                        task.id,
                        CANCEL_GRACE_SECONDS,
                    )
            if turn.terminal:
                return
        else:
            await self._cancel_elsewhere(context, task)
        await task_updater.cancel()

    async def _cancel_elsewhere(self, context: RequestContext, task: Task) -> None:
        """Tell the task's executor about the cancel of a task with no turn here; it waits for nothing."""
        try:
            executor = self.pool.get(routed_extension_of(task))
        except InvalidParamsError:
            return
        try:
            await executor.cancel(self._config(context, task))
        except UnsupportedOperationError:
            logger.debug("The executor of task %s cannot stop a turn", task.id)
        except Exception:
            logger.exception("Telling the executor of task %s about its cancel failed", task.id)

    @staticmethod
    async def _setup_runtime_context(context: RequestContext) -> ResponseServiceParameters:
        """Set runtime context and return its verified invocation activation."""
        runtime_context = AionRuntimeContextBuilder.from_request_context(context)
        if runtime_context is None:
            runtime_context = AionRuntimeContext()
        runtime_context = AionRuntimeContext(
            inbox=runtime_context.inbox,
            event=runtime_context.event,
            distribution_extension_payload=runtime_context.distribution_extension_payload,
            extensions=runtime_context.extensions,
            callback_attribution=callback_attribution(context.call_context, runtime_context),
            **runtime_context.graph_kwargs,
        )
        await AionRuntimeContextRegistry.aset_current_context(runtime_context)
        return ResponseServiceParameters(runtime_context.extensions.activated_uris)

    def _config(self, context: RequestContext, task: Task) -> ExecutionConfig:
        """The execution of the task's turn: the task, its context, and whose framework state it uses."""
        return ExecutionConfig(
            task_id=task.id,
            context_id=task.context_id,
            state_scope=StateScope.for_call(self.agent.id, context.call_context, self.agent.owner_resolver),
        )

    def _admitted_route(self, context: Optional[RequestContext]) -> Optional[str]:
        """The extension the request handler admitted a new task to, if it is one this server serves."""
        state = getattr(getattr(context, "call_context", None), "state", None)
        uri = state.get(EXTENSION_ROUTE_STATE_KEY) if isinstance(state, Mapping) else None
        return uri if isinstance(uri, str) and uri in self.pool.extensions else None

    @staticmethod
    async def _get_task_for_execution(context: RequestContext) -> Optional[Tuple[Task, bool]]:
        """Decide what this request should execute, if anything.

        An existing task is continued whenever it is not terminal. That covers
        both an interrupted task, which resumes where it stopped, and one that
        is still active, which the SDK reaches by dequeuing a second request
        for the same task: ``ActiveTask`` serialises requests through
        ``_request_queue`` and treats a follow-up message as the next turn, not
        as an error. Whether continuing means resuming a suspended run or
        simply taking another turn is the task's executor's decision, not this
        request executor's.

        A terminal task is not executed. Reaching one here is a narrow race
        rather than the normal path — the SDK already rejects a terminal task
        in ``ActiveTask.start`` and in ``subscribe``, and shuts the request
        queue down once a terminal state is consumed. The race is that the
        producer takes a request off the queue before that shutdown lands. The
        request is dropped silently instead of raising, because an exception
        out of ``execute`` is not a per-request rejection: the SDK producer
        reads it as agent failure, persists FAILED for the whole task and
        propagates the error to every subscriber.

        A new task is built as a2a-sdk builds one, from the user message
        alone. The request's ``params.metadata`` is not copied into it: it
        carries what the caller sent for this turn - the daemon payload with
        its configuration variables among it - and a task's metadata is
        stored, journaled and returned to every reader of the task. The agent
        reads request metadata where it belongs: ``RequestContext.metadata``,
        the extension payloads the runtime context collects, and
        ``runtime.context.inbox``. ``task.metadata`` holds only what the
        server and its executors write, so a client cannot set a
        server-owned key such as ``aion:settledReason`` either.

        A new task's SUBMITTED status is stamped here, when the task is made.
        Every status of the turn is stamped when it is made - ``TaskUpdater``
        does it as it creates the event - while the event consumer stores this
        task later, concurrently with the turn; a time left for the consumer
        to fill in could fall after the turn's first WORKING.

        Args:
            context: Request context with optional current task

        Returns:
            Tuple of (task, is_new_task), or None when there is nothing to run.
        """
        current_task = context.current_task

        if current_task is not None:
            if current_task.status.state in TERMINAL_TASK_STATES:
                logger.warning(
                    "Skipping request for task %s already in terminal state %s",
                    current_task.id,
                    current_task.status.state,
                )
                return None

            context.current_task = current_task
            return current_task, False

        # Create new task
        task = new_task_from_user_message(context.message)
        task.status.timestamp.GetCurrentTime()
        context.current_task = task

        set_task_id(task.id)
        return task, True

    @staticmethod
    def _validate_request(context: RequestContext) -> bool:
        """Validate the request context.

        Args:
            context: Request context to validate

        Returns:
            True if validation fails, False if valid
        """
        # Add validation logic as needed
        return False


async def _close(events: Any) -> None:
    """Close the executor's stream, so a turn that ends early stops the executor's work."""
    aclose = getattr(events, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        logger.exception("Closing an executor's stream failed")
