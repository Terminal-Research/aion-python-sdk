"""Framework-agnostic A2A executor for AionAgent."""
import logging
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Task
from a2a.helpers import new_task_from_user_message
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.telemetry import trace_function
from aion.core.runtime import AionRuntimeContextBuilder, ExtensionActivationError
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.a2a.utils import is_task_interrupted
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
        self._task_updater: TaskUpdater | None = None
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
        """Build the executor, keeping only extension handlers available for this agent."""
        candidates = discover_extension_task_handlers()
        available = [
            handler for handler in candidates
            if await handler.is_available(aion_agent.config)
        ]
        return cls(aion_agent, file_transformer=file_transformer, extension_handlers=available)

    @trace_function
    async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
    ) -> None:
        error = self._validate_request(context)
        if error:
            raise InvalidParamsError()

        task, is_new_task = await self._get_task_for_execution(context)
        self._task_updater = TaskUpdater(event_queue, task.id, task.context_id)

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

        try:
            pipeline = AionEventPipeline(event_queue, self._task_updater, self._file_transformer)
            async for agent_event in produce_events(context=context):
                await pipeline.process(agent_event)

        except Exception as ex:
            logger.exception("Execution failed")
            raise InternalError() from ex

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
        try:
            await cancel(context=context)
        except UnsupportedOperationError:
            logger.debug("Handler does not support cancellation, proceeding with A2A cancel")

        task_updater = TaskUpdater(event_queue, task.id, task.context_id)
        await task_updater.cancel()

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
    async def _get_task_for_execution(context: RequestContext) -> Tuple[Task, bool]:
        """Get or create a task for execution.

        Logic:
        1. If current_task exists and is interrupted -> resume it
        2. If current_task exists but not interrupted -> error
        3. If no current_task -> create new task

        Args:
            context: Request context with optional current task

        Returns:
            Tuple of (task, is_new_task)

        Raises:
            InvalidParamsError: if task is in a terminal state
        """
        current_task = context.current_task

        if current_task is not None:
            if is_task_interrupted(current_task):
                context.current_task = current_task
                return current_task, False
            else:
                raise InvalidParamsError(
                    message=f"Task {current_task.id} is in terminal state: "
                            f"{current_task.status.state}"
                )

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
