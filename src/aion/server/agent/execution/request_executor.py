"""Framework-agnostic A2A executor for AionAgent."""
import logging
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, Task
from a2a.helpers import new_task_from_user_message
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.telemetry import trace_function
from aion.core.a2a.enums import A2AMetadataKey
from aion.core.runtime import (
    AionRuntimeContextBuilder,
    ExtensionActivationError,
    aion_a2a_extension_registry,
)
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.exceptions import AionAuthenticationError, InvalidPrincipalError
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.agent.aion_agent import AionAgent
from aion.server.agent.execution.scope import set_task_id
from aion.server.files.a2a import A2AFileTransformer
from collections.abc import Callable, Iterable
from typing import Literal, Optional, Tuple

from .event_pipeline import AionEventPipeline, OutboundFileNotStoredError
from .context.attribution import callback_attribution
from .extensions import (
    ExtensionPreflightError,
    ExtensionTaskHandler,
    discover_extension_task_handlers,
)

logger = logging.getLogger(__name__)

# Task metadata key recording the extension handler a task is routed to. See
# docs/development/extension-exposure.md, "Task-routing".
_ROUTED_EXTENSION = A2AMetadataKey.ROUTED_EXTENSION.value


CRASH_REASON = (
    "The agent stopped on an unexpected error. Its log has the details under task {task_id}."
)
"""What the task of a crashed turn says: that it crashed, and where to look.

The exception's own text stays in the log; it may name paths, hosts or
whatever the agent interpolated into it.
"""


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


class _FileStoringEventQueue(EventQueue):
    """The request's event queue, with inline files stored on the way in.

    What a cancel hook publishes reaches the queue directly rather than
    through the event pipeline, so this queue stores its files the way the
    pipeline stores a run's. A file that cannot be stored is dropped, with its
    reason in the transformer's log line, and the task is canceled all the
    same: cancellation is the outcome the caller asked for.
    """

    def __init__(self, queue: EventQueue, transformer: A2AFileTransformer) -> None:
        self._queue = queue
        self._transformer = transformer

    async def enqueue_event(self, event) -> None:
        """Store the event's inline files, then publish what is left of it."""
        transform = await self._transformer.transform(event)
        if transform.event is not None:
            await self._queue.enqueue_event(transform.event)


class AionAgentRequestExecutor(AgentExecutor):
    """A2A executor adapter for AionAgent.

    Bridges A2A protocol's AgentExecutor interface with AionAgent.
    Plugins are responsible for producing A2A events directly —
    this executor simply enqueues them.

    If an A2AFileTransformer is provided, inline (base64) file parts in
    everything the agent sends out - status and artifact updates, and the
    Message or Task an agent returns through its outbox - are stored and
    replaced with URL parts before being delivered. A file that could not be
    stored fails the task: the rest of its event still reaches the client and
    the run stops. The handler that produced the event is asked to forget it
    (``discard_undelivered``), so a copy the framework kept cannot be read
    back by a later turn.

    A turn that fails says why on its FAILED status, which the stream closes
    with, the unary call answers and a later `tasks/get` returns: a file that
    could not be stored and any error carrying a ``public_reason`` - the
    agent's own ``TaskFailedError``, or an SDK error naming what the
    deployment lacks - give their sentence, and the request ends normally.
    Any other error is a crash: the task names it as one and points at the
    log, and the error is raised for a2a-sdk to report.
    What a cancel hook publishes, and the message it closes the CANCELED with,
    have their files stored the same way; there a file that could not be
    stored is dropped and the task is canceled regardless. Inline bytes never
    reach the task record.
    """

    def __init__(
            self,
            aion_agent: AionAgent,
            file_transformer: Optional[A2AFileTransformer] = None,
            extension_handlers: Iterable[ExtensionTaskHandler] = (),
    ):
        self.agent = aion_agent
        self._file_transformer = file_transformer
        self._extension_handlers: dict[str, ExtensionTaskHandler] = {
            handler.uri: handler for handler in extension_handlers
        }

    @classmethod
    async def create(
            cls,
            aion_agent: AionAgent,
            file_transformer: Optional[A2AFileTransformer] = None,
    ) -> "AionAgentRequestExecutor":
        """Build the executor, keeping only extension handlers available for this agent.

        A handler that reports itself unavailable is recorded in the central
        extension registry (mark_unavailable) with its own reason - the
        request-time verification pipeline then rejects any request declaring
        that extension, uniformly with extensions that have no handler at
        all. The executor keeps no availability state of its own.
        """
        available: list[ExtensionTaskHandler] = []
        for handler in discover_extension_task_handlers():
            result = await handler.availability(aion_agent.config)
            if result.available:
                available.append(handler)
                continue
            reason = result.reason or f"extension '{handler.uri}' is not available on this agent"
            enabled = getattr(aion_agent.config, "enabled_extensions", None) or ()
            if handler.uri in enabled:
                aion_a2a_extension_registry.mark_unavailable(handler.uri, reason)
                logger.warning(
                    "Extension '%s' task handler is unavailable - requests "
                    "declaring it will be rejected: %s",
                    handler.uri,
                    reason,
                )
        return cls(
            aion_agent,
            file_transformer=file_transformer,
            extension_handlers=available,
        )

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

        operation: Literal["stream", "resume"] = "stream" if is_new_task else "resume"
        try:
            produce_events = await self._resolve(task, operation, context)
        except ExtensionPreflightError as ex:
            raise InvalidParamsError(message=str(ex)) from ex

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
        try:
            async for agent_event in produce_events(context=context):
                await pipeline.process(agent_event)

        except OutboundFileNotStoredError as ex:
            # Each file's reason is already logged, one WARNING line per file.
            await self._discard_undelivered(task, context, ex.event)
            await self._close_failed_task(task_updater, pipeline, reason=ex.public_reason)
        except Exception as ex:
            reason = public_reason_of(ex)
            if reason is not None:
                # An outcome its raiser put in words for the caller - the
                # agent itself, or the SDK naming what a deployment lacks -
                # not a crash: the task fails saying so, and the request ends
                # normally. Raising would have a2a-sdk log it as an unexpected
                # error, twice and with a traceback.
                logger.warning("Task failed: %s", ex)
                await self._close_failed_task(task_updater, pipeline, reason=reason)
                return
            # A crash. a2a-sdk logs the error raised here with its traceback,
            # the original exception included as its cause.
            crash = CRASH_REASON.format(task_id=task.id)
            await self._close_failed_task(task_updater, pipeline, reason=crash)
            raise InternalError(message=crash) from ex

    async def _discard_undelivered(
            self,
            task: Task,
            context: RequestContext,
            event: Event,
    ) -> None:
        """Let the producer of an event that never reached the task forget it.

        Runs before the task is closed, so a turn that follows the FAILED
        state cannot read back what the client never received. A handler
        without ``discard_undelivered`` keeps no copy of its own. Failures are
        logged and swallowed: the task fails either way.
        """
        discard = getattr(self._routed_handler(task), "discard_undelivered", None)
        if discard is None:
            return
        try:
            await discard(context, event)
        except Exception:  # noqa: BLE001 - must not mask the original failure
            logger.exception("Could not discard an event that was not delivered")

    @staticmethod
    async def _close_failed_task(
            task_updater: TaskUpdater,
            pipeline: AionEventPipeline,
            reason: Optional[str] = None,
    ) -> None:
        """Put a crashed execution's task into a terminal FAILED state.

        The JSON-RPC error alone only reaches the caller of this one request:
        the task itself would stay WORKING in the store forever, so a later
        `tasks/get` — or any client that reconnects — sees work that is
        permanently in progress. Skipped when the producer already emitted a
        terminal state, since that outcome is the authoritative one.

        ``reason`` becomes the FAILED status's message, so the stream, the
        unary answer and a later `tasks/get` all say why the task failed. It
        is always a sentence written for the caller - a public reason, or the
        crash notice - never an exception's own text. Failures here are
        swallowed so they cannot mask the original error.

        Args:
            task_updater: Updater bound to the failed execution's task.
            pipeline: Pipeline that processed the execution's events, consulted
                for whether a terminal state was already reported.
            reason: Sentence the caller reads, or None for a bare FAILED.
        """
        if pipeline.terminal_seen:
            return
        message = task_updater.new_agent_message([Part(text=reason)]) if reason else None
        try:
            await task_updater.failed(message=message)
        except Exception:  # noqa: BLE001 - must not mask the original failure
            logger.exception("Could not mark the task as failed after an execution error")

    async def cancel(
            self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Cancel with the current request's attribution, restoring any parent scope."""
        previous = await AionRuntimeContextRegistry.aget_current_context()
        try:
            await self._setup_runtime_context(context)
            await self._cancel(context, event_queue)
        finally:
            await AionRuntimeContextRegistry.aset_current_context(previous)

    async def _cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Request cancellation of an ongoing task.

        Resolves the task from context, validates it is in a cancelable state,
        delegates to the framework adapter, and emits a terminal CANCELED event.

        Args:
            context: A2A request context with task to cancel
            event_queue: Queue for publishing cancellation events

        Raises:
            TaskNotFoundError: if context carries no task
            TaskNotCancelableError: if the task is already in a terminal state
            UnsupportedOperationError: if the framework does not support cancellation
        """
        task = context.current_task
        if task is None:
            raise TaskNotFoundError()

        if task.status.state in TERMINAL_TASK_STATES:
            raise TaskNotCancelableError(
                message=f"Task {task.id} cannot be canceled - current state: {task.status.state}"
            )

        cancel = await self._resolve(task, "cancel")
        cancel_queue = (
            _FileStoringEventQueue(event_queue, self._file_transformer)
            if self._file_transformer is not None
            else event_queue
        )
        cancel_message = None
        try:
            cancel_message = await cancel(context=context, event_queue=cancel_queue)
        except UnsupportedOperationError:
            logger.debug("Handler does not support cancellation, proceeding with A2A cancel")

        # The request's queue was already handed to the extension handler
        # above, so any artifact it needed to publish (e.g. undelivered work
        # rescued as a bundle) went out before this point, its files stored on
        # the way - the event consumer stops accepting events the moment a
        # terminal state lands, so anything queued after
        # `task_updater.cancel()` below would be dropped. The returned message
        # is the closing text/data for that terminal event itself, and its
        # files are stored the same way. Framework adapters return None, so
        # this is inert for them; anything that is not a Message is ignored
        # rather than trusted into the A2A event.
        if not isinstance(cancel_message, Message):
            cancel_message = None
        elif self._file_transformer is not None:
            cancel_message = (await self._file_transformer.transform(cancel_message)).event

        task_updater = TaskUpdater(event_queue, task.id, task.context_id)
        await task_updater.cancel(message=cancel_message)

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

    async def _resolve(
            self,
            task: Task,
            operation: Literal["stream", "resume", "cancel"],
            context: Optional[RequestContext] = None,
    ) -> Callable:
        """Resolve the callable for an operation on the task's routed handler.

        "stream" is only ever requested for a newly created task: it decides
        routing from the live runtime context and persists that decision onto
        the task's metadata. "resume"/"cancel" are requested for an
        already-routed task: they recall the decision from that metadata
        instead of re-deciding it. Either way, falls back to the agent's own
        framework adapter when no extension is routed for the task.

        A new task starts without metadata (see _get_task_for_execution), so
        the routing key it carries is the one written here: only the server
        decides which handler a task belongs to.

        Routing itself is a plain lookup against
        AionRuntimeContext.extensions - the set of extensions already
        collected and verified (schema + co-activation requirements) during
        data prep in AionRuntimeContextBuilder. No activation or validation
        logic lives here.

        For "stream"/"resume" routed to an extension handler, this also runs
        that handler's per-request preflight (see ExtensionPreflightError) -
        here, not in execute(), because this is where `handler` is known, and
        it still runs before the task is persisted/enqueued (see execute()).
        `context` is required for those two operations; "cancel" passes none,
        since a cancelled task already exists and has nothing left to preflight.
        """
        handler = self.agent
        if operation == "stream":
            if self._extension_handlers:
                runtime_context = await AionRuntimeContextRegistry.aget_current_context()
                if runtime_context is not None:
                    matched = next(
                        (uri for uri in self._extension_handlers if runtime_context.is_extension_active(uri)),
                        None,
                    )
                    if matched:
                        task.metadata[_ROUTED_EXTENSION] = matched
                        handler = self._extension_handlers[matched]

        else:
            handler = self._routed_handler(task)

        if operation in ("stream", "resume") and handler is not self.agent:
            preflight = getattr(handler, "preflight", None)
            if preflight is not None:
                await preflight(context)
        return getattr(handler, operation)

    def _routed_handler(self, task: Task):
        """The extension handler a routed task is bound to, or the agent itself."""
        if task.HasField("metadata") and _ROUTED_EXTENSION in task.metadata:
            return self._extension_handlers.get(task.metadata[_ROUTED_EXTENSION], self.agent)
        return self.agent

    @staticmethod
    async def _get_task_for_execution(context: RequestContext) -> Optional[Tuple[Task, bool]]:
        """Decide what this request should execute, if anything.

        An existing task is continued whenever it is not terminal. That covers
        both an interrupted task, which resumes where it stopped, and one that
        is still active, which the SDK reaches by dequeuing a second request
        for the same task: ``ActiveTask`` serialises requests through
        ``_request_queue`` and treats a follow-up message as the next turn, not
        as an error. Whether continuing means resuming a suspended run or
        simply taking another turn is the framework adapter's decision, not
        this executor's.

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
        server and trusted handlers write, so a client cannot set a
        server-owned key such as ``aion:settledReason`` either.

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
