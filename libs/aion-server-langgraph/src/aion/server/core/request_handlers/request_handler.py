import asyncio
from typing import cast

from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.default_request_handler import TERMINAL_TASK_STATES
from a2a.server.tasks import TaskManager, ResultAggregator
from a2a.types import MessageSendParams, Task, InvalidParamsError, TaskNotFoundError
from a2a.utils.errors import ServerError
from a2a.utils.telemetry import trace_class, SpanKind

from .interfaces import IRequestHandler
from aion.server.types import (
    GetContextParams,
    GetContextsListParams,
    Conversation,
    ContextsList
)
from aion.server.tasks import store_manager, AionTaskManager
from aion.server.utils import ConversationBuilder


@trace_class(kind=SpanKind.SERVER)
class AionRequestHandler(DefaultRequestHandler, IRequestHandler):
    """Request handler implementation for Aion management operations."""

    async def _setup_message_execution(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> tuple[TaskManager, str, EventQueue, ResultAggregator, asyncio.Task]:
        """ !! Overrides default message execution setup. !!

        Common setup logic for both streaming and non-streaming message handling.

        Returns:
            A tuple of (task_manager, task_id, queue, result_aggregator, producer_task)
        """
        # Create task manager and validate existing task
        task_manager = AionTaskManager(
            task_id=params.message.task_id,
            context_id=params.message.context_id,
            task_store=self.task_store,
            initial_message=params.message,
        )
        if not task_manager.task_id:
            await task_manager.auto_discover_and_assign_task(interrupted=True)

        task: Task | None = await task_manager.get_task()
        if task:
            if task.status.state in TERMINAL_TASK_STATES:
                raise ServerError(
                    error=InvalidParamsError(
                        message=f'Task {task.id} is in terminal state: {task.status.state}'
                    )
                )

            task = task_manager.update_with_message(params.message, task)
        elif params.message.task_id:
            raise ServerError(
                error=TaskNotFoundError(
                    message=f'Task {params.message.task_id} was specified but does not exist'
                )
            )

        # Build request context
        request_context = await self._request_context_builder.build(
            params=params,
            task_id=task.id if task else None,
            context_id=params.message.context_id,
            task=task,
            context=context,
        )

        task_id = cast('str', request_context.task_id)
        # Always assign a task ID. We may not actually upgrade to a task, but
        # dictating the task ID at this layer is useful for tracking running
        # agents.

        if (
            self._push_config_store
            and params.configuration
            and params.configuration.push_notification_config
        ):
            await self._push_config_store.set_info(
                task_id, params.configuration.push_notification_config
            )

        queue = await self._queue_manager.create_or_tap(task_id)
        result_aggregator = ResultAggregator(task_manager)
        # TODO: to manage the non-blocking flows.
        producer_task = asyncio.create_task(
            self._run_event_stream(request_context, queue)
        )
        await self._register_producer(task_id, producer_task)

        return task_manager, task_id, queue, result_aggregator, producer_task

    async def on_get_context(
            self,
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

    async def on_get_contexts_list(
            self,
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
