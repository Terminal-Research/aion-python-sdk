from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from a2a.server.request_handlers import JSONRPCHandler, prepare_response_object
from a2a.types import JSONRPCErrorResponse, InternalError
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
