from __future__ import annotations

from typing import TYPE_CHECKING, Optional, AsyncIterable

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import JSONRPCHandler, prepare_response_object
from a2a.types import JSONRPCErrorResponse, InternalError, SendStreamingMessageRequest, SendStreamingMessageResponse, \
    Task, Message, TaskArtifactUpdateEvent, TaskStatusUpdateEvent, SendStreamingMessageSuccessResponse, \
    TaskPushNotificationConfig, SetTaskPushNotificationConfigSuccessResponse, SetTaskPushNotificationConfigResponse, \
    SetTaskPushNotificationConfigRequest
from a2a.utils.errors import ServerError

from aion.server.types import (
    GetContextRequest,
    GetContextsListRequest,
    GetContextResponse,
    GetContextSuccessResponse,
    GetContextsListResponse,
    GetContextsListSuccessResponse,
    ContextsList,
    Conversation
)
from aion.server.utils import validate_agent_id, validate_operation
from .call_context import AionServerCallContext

if TYPE_CHECKING:
    from aion.server.core.request_handlers import AionRequestHandler


class AionJSONRPCHandler(JSONRPCHandler):
    """Extended JSON-RPC handler with custom methods for Aion context management."""
    request_handler: AionRequestHandler

    async def on_get_context(
            self,
            request: GetContextRequest,
            context: Optional[AionServerCallContext] = None,
    ) -> GetContextResponse:
        """Handle get context request to retrieve conversation data.

        Args:
            request: Get context request with parameters
            context: Optional server call context

        Returns:
            Context response with conversation data or error
        """
        try:
            conversation_obj = await self.request_handler.on_get_context(
                request.params, context
            )
            return prepare_response_object(
                request_id=request.id,
                response=conversation_obj,
                success_response_types=(Conversation,),
                success_payload_type=GetContextSuccessResponse,
                response_type=GetContextResponse,
            )
        except ServerError as e:
            return GetContextResponse(
                root=JSONRPCErrorResponse(
                    id=request.id, error=e.error if e.error else InternalError()
                )
            )

    async def on_get_contexts_list(
            self,
            request: GetContextsListRequest,
            context: Optional[AionServerCallContext] = None,
    ) -> GetContextsListResponse:
        """Handle get contexts list request to retrieve available context IDs.

        Args:
            request: Get contexts list request with parameters
            context: Optional server call context

        Returns:
            Contexts list response with context IDs or error
        """
        try:
            context_ids = await self.request_handler.on_get_contexts_list(
                request.params, context
            )
            return prepare_response_object(
                request_id=request.id,
                response=context_ids,
                success_response_types=(ContextsList,),
                success_payload_type=GetContextsListSuccessResponse,
                response_type=GetContextsListResponse,
            )
        except ServerError as e:
            return GetContextsListResponse(
                root=JSONRPCErrorResponse(
                    id=request.id, error=e.error if e.error else InternalError()
                )
            )

    async def on_message_send_stream(
            self,
            request: SendStreamingMessageRequest,
            context: ServerCallContext | None = None,
    ) -> AsyncIterable[SendStreamingMessageResponse]:
        """Handles the 'message/stream' JSON-RPC method.

        Yields response objects as they are produced by the underlying handler's stream.

        Args:
            request: The incoming `SendStreamingMessageRequest` object.
            context: Context provided by the server.

        Yields:
            `SendStreamingMessageResponse` objects containing streaming events
            (Task, Message, TaskStatusUpdateEvent, TaskArtifactUpdateEvent)
            or JSON-RPC error responses if a `ServerError` is raised.
        """
        agent = validate_agent_id(context.agent_id)
        validate_operation(
            agent.card.capabilities.streaming,
            'Streaming is not supported by the agent')

        try:
            async for event in self.request_handler.on_message_send_stream(
                    request.params, context
            ):
                yield prepare_response_object(
                    request.id,
                    event,
                    (
                        Task,
                        Message,
                        TaskArtifactUpdateEvent,
                        TaskStatusUpdateEvent,
                    ),
                    SendStreamingMessageSuccessResponse,
                    SendStreamingMessageResponse,
                )
        except ServerError as e:
            yield SendStreamingMessageResponse(
                root=JSONRPCErrorResponse(
                    id=request.id, error=e.error if e.error else InternalError()
                )
            )

    async def set_push_notification_config(
            self,
            request: SetTaskPushNotificationConfigRequest,
            context: ServerCallContext | None = None,
    ) -> SetTaskPushNotificationConfigResponse:
        """Handles the 'tasks/pushNotificationConfig/set' JSON-RPC method.

        Requires the agent to support push notifications.

        Args:
            request: The incoming `SetTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A `SetTaskPushNotificationConfigResponse` object containing the config or a JSON-RPC error.

        Raises:
            ServerError: If push notifications are not supported by the agent
                (due to the `@validate` decorator).
        """
        agent = validate_agent_id(context.agent_id)
        validate_operation(
            agent.card.capabilities.push_notifications,
            'Push notifications are not supported by the agent')

        try:
            config = (
                await self.request_handler.on_set_task_push_notification_config(
                    request.params, context
                )
            )
            return prepare_response_object(
                request.id,
                config,
                (TaskPushNotificationConfig,),
                SetTaskPushNotificationConfigSuccessResponse,
                SetTaskPushNotificationConfigResponse,
            )
        except ServerError as e:
            return SetTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(
                    id=request.id, error=e.error if e.error else InternalError()
                )
            )
