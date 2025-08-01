from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.utils.telemetry import trace_class, SpanKind

from .interfaces import IRequestHandler
from aion.server.types import (
    GetContextParams,
    GetContextsListParams,
    Conversation,
    ContextsList
)
from aion.server.tasks import store_manager
from aion.server.utils import ConversationBuilder


@trace_class(kind=SpanKind.SERVER)
class AionRequestHandler(DefaultRequestHandler, IRequestHandler):
    """Request handler implementation for Aion management operations."""

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
