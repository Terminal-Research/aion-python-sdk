from a2a.server.agent_execution import RequestContext
from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.types import SendMessageRequest, TaskNotFoundError, TaskState
from a2a.utils.task import validate_history_length
from aion.shared.types import ContextsList, Conversation, GetContextParams, GetContextsListParams
from collections.abc import AsyncGenerator
from functools import wraps
from typing import cast, override

from aion.server.agent.agent_execution.active_task_registry import AionActiveTaskRegistry
from aion.server.tasks import store_manager
from aion.server.utils import ConversationBuilder
from .request_preprocessors import A2ARequestPreprocessor

# Final states that should trigger sending the completed Task to the client
_FINAL_STATES = frozenset({
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
})


def _with_preprocessors(method):
    @wraps(method)
    async def wrapper(self, params, *args, **kwargs):
        for preprocessor in self._preprocessors:
            await preprocessor.process(params)
        try:
            return await method(self, params, *args, **kwargs)
        except Exception:
            for preprocessor in reversed(self._preprocessors):
                await preprocessor.rollback()
            raise
    return wrapper


class AionRequestHandler(DefaultRequestHandlerV2):
    """Request handler implementation for Aion management operations."""

    def __init__(self, *args, preprocessors: list[A2ARequestPreprocessor] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._active_task_registry = AionActiveTaskRegistry(
            agent_executor=self.agent_executor,
            task_store=self.task_store,
            push_sender=self._push_sender,
        )
        self._preprocessors = preprocessors or []

    @override
    @_with_preprocessors
    async def _setup_active_task(
            self,
            params: SendMessageRequest,
            call_context: ServerCallContext,
    ) -> tuple[ActiveTask, RequestContext]:
        validate_history_length(params.configuration)

        original_task_id = params.message.task_id or None
        original_context_id = params.message.context_id or None

        if original_task_id:
            task = await self.task_store.get(original_task_id, call_context)
            if not task:
                raise TaskNotFoundError(f'Task {original_task_id} not found')

        # Build context to resolve or generate missing IDs
        request_context = await self._request_context_builder.build(
            params=params,
            task_id=original_task_id,
            context_id=original_context_id,
            # We will get the task when we have to process the request to avoid concurrent read/write issues.
            task=None,
            context=call_context,
        )

        task_id = cast('str', request_context.task_id)
        context_id = cast('str', request_context.context_id)

        if (
                self._push_config_store
                and params.configuration.HasField("task_push_notification_config")
        ):
            await self._push_config_store.set_info(
                task_id,
                params.configuration.task_push_notification_config,
                call_context,
            )

        active_task = await self._active_task_registry.get_or_create(
            task_id,
            context_id=context_id,
            call_context=call_context,
            create_task_if_missing=True,
        )

        return active_task, request_context

    @staticmethod
    async def on_get_context(
            params: GetContextParams,
            context: ServerCallContext | None = None
    ) -> Conversation:
        """Get conversation context by ID.

        Args:
            params: Parameters containing context ID
            context: Optional server call context

        Returns:
            Conversation object with context data
        """
        task_store = store_manager.get_store()
        tasks = await task_store.get_context_tasks(
            context_id=params.context_id,
            limit=params.history_length,
            offset=params.history_offset)

        return ConversationBuilder.build_from_tasks(context_id=params.context_id, tasks=tasks)

    @staticmethod
    async def on_get_contexts_list(
            params: GetContextsListParams,
            context: ServerCallContext | None = None
    ) -> ContextsList:
        """Get list of available context IDs.

        Args:
            params: Parameters for contexts list request
            context: Optional server call context

        Returns:
            List of available context IDs
        """
        task_store = store_manager.get_store()
        context_ids = await task_store.get_context_ids(
            limit=params.history_length,
            offset=params.history_offset)
        return ContextsList.model_validate(context_ids)

    async def on_message_send_stream(
            self,
            params: SendMessageRequest,
            context: ServerCallContext,
    ) -> AsyncGenerator[Event]:
        """Stream handler that emits final Task before stream closes.

        Overrides DefaultRequestHandler to emit the final Task object after
        all other events have been streamed. This ensures the client receives
        a complete Task snapshot with all accumulated state at stream end.
        """
        async for event in super().on_message_send_stream(params, context):
            yield event

        # After stream completes, emit final Task if it's in a final state
        task_id = params.message.task_id
        if task_id:
            final_task = await self.task_store.get(task_id, context)
            if final_task and final_task.status.state in _FINAL_STATES:
                yield final_task
