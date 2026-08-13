"""Framework-agnostic A2A executor for AionAgent."""
import logging
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Task
from a2a.helpers import new_task_from_user_message
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.telemetry import trace_function
from aion.core.runtime import (
    AionRuntimeContextBuilder,
    ExtensionActivationError,
    aion_a2a_extension_registry,
)
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.agent.aion_agent import AionAgent
from aion.server.agent.execution.scope import set_task_id
from aion.server.files.a2a import A2AFileTransformer
from collections.abc import Callable, Iterable
from typing import Literal, Optional, Tuple

from .event_pipeline import AionEventPipeline
from .extensions import (
    ExtensionTaskHandler,
    ROUTED_EXTENSION_METADATA_KEY,
    discover_extension_task_handlers,
)

logger = logging.getLogger(__name__)


class AionAgentRequestExecutor(AgentExecutor):
    """A2A executor adapter for AionAgent.

    Bridges A2A protocol's AgentExecutor interface with AionAgent.
    Plugins are responsible for producing A2A events directly —
    this executor simply enqueues them.

    If an A2AFileTransformer is provided, inline (base64) file parts in
    outgoing events are transparently replaced with URL parts before
    being enqueued. The actual upload happens in the background.
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
            await self._setup_runtime_context(context)
        except ExtensionActivationError as ex:
            raise InvalidParamsError(message=str(ex)) from ex

        operation: Literal["stream", "resume"] = "stream" if is_new_task else "resume"
        produce_events = await self._resolve(task, operation)

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
        )
        try:
            async for agent_event in produce_events(context=context):
                await pipeline.process(agent_event)

        except Exception as ex:
            logger.exception("Execution failed")
            await self._close_failed_task(task_updater, pipeline)
            raise InternalError() from ex

    @staticmethod
    async def _close_failed_task(
            task_updater: TaskUpdater,
            pipeline: AionEventPipeline,
    ) -> None:
        """Put a crashed execution's task into a terminal FAILED state.

        The JSON-RPC error alone only reaches the caller of this one request:
        the task itself would stay WORKING in the store forever, so a later
        `tasks/get` — or any client that reconnects — sees work that is
        permanently in progress. Skipped when the producer already emitted a
        terminal state, since that outcome is the authoritative one.

        No message is attached: the exception text is internal detail, and it
        is already logged. Failures here are swallowed so they cannot mask the
        original error being propagated by the caller.

        Args:
            task_updater: Updater bound to the failed execution's task.
            pipeline: Pipeline that processed the execution's events, consulted
                for whether a terminal state was already reported.
        """
        if pipeline.terminal_seen:
            return
        try:
            await task_updater.failed()
        except Exception:  # noqa: BLE001 - must not mask the original failure
            logger.exception("Could not mark the task as failed after an execution error")

    async def drain(self) -> None:
        """Wait for all in-flight background uploads to complete.

        Should be called during graceful shutdown to ensure no uploads are
        silently dropped when the server stops.
        """
        if self._file_transformer is not None:
            await self._file_transformer.drain()

    async def cancel(
            self, context: RequestContext, event_queue: EventQueue
    ) -> None:
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
        cancel_message = None
        try:
            cancel_message = await cancel(context=context)
        except UnsupportedOperationError:
            logger.debug("Handler does not support cancellation, proceeding with A2A cancel")

        # An extension handler may hand back a closing message to carry on the
        # terminal event - the only channel it has, since the event consumer
        # stops accepting events the moment a terminal state lands. Framework
        # adapters return None, so this is inert for them; anything that is not
        # a Message is ignored rather than trusted into the A2A event.
        if not isinstance(cancel_message, Message):
            cancel_message = None

        task_updater = TaskUpdater(event_queue, task.id, task.context_id)
        await task_updater.cancel(message=cancel_message)

    @staticmethod
    async def _setup_runtime_context(context: RequestContext) -> None:
        """Build and set Aion runtime context at server level for all executors."""
        runtime_context = AionRuntimeContextBuilder.from_request_context(context)
        if runtime_context:
            await AionRuntimeContextRegistry.aset_current_context(runtime_context)

    async def _resolve(
            self,
            task: Task,
            operation: Literal["stream", "resume", "cancel"],
    ) -> Callable:
        """Resolve the callable for an operation on the task's routed handler.

        "stream" is only ever requested for a newly created task: it decides
        routing from the live runtime context and persists that decision onto
        the task's metadata. "resume"/"cancel" are requested for an
        already-routed task: they recall the decision from that metadata
        instead of re-deciding it. Either way, falls back to the agent's own
        framework adapter when no extension is routed for the task.

        Routing itself is a plain lookup against
        AionRuntimeContext.extensions - the set of extensions already
        collected and verified (schema + co-activation requirements) during
        data prep in AionRuntimeContextBuilder. No activation or validation
        logic lives here.
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
                        task.metadata[ROUTED_EXTENSION_METADATA_KEY] = matched
                        handler = self._extension_handlers[matched]

        elif task.HasField("metadata") and ROUTED_EXTENSION_METADATA_KEY in task.metadata:
            handler = self._extension_handlers.get(task.metadata[ROUTED_EXTENSION_METADATA_KEY], self.agent)
        return getattr(handler, operation)

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
        if context.metadata:
            task.metadata = context.metadata
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
