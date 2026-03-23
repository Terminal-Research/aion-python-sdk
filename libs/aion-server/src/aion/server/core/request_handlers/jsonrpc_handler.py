from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import JSONRPCHandler, prepare_response_object
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.utils.errors import A2AError, InternalError
from aion.shared.types import (
    GetContextRequest,
    GetContextsListRequest,
)

if TYPE_CHECKING:
    from aion.server.core.request_handlers import AionRequestHandler


class AionJSONRPCHandler(JSONRPCHandler):
    """Extended JSON-RPC handler with custom methods for Aion context management."""
    request_handler: AionRequestHandler

    @classmethod
    def from_existing(cls, handler: JSONRPCHandler) -> "AionJSONRPCHandler":
        if isinstance(handler, cls):
            return handler

        new = cls.__new__(cls)
        new.__dict__ = handler.__dict__
        return new

    async def on_get_context(
            self,
            request: GetContextRequest,
            context: Optional[ServerCallContext] = None,
    ) -> dict[str, Any]:
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
                response=conversation_obj.model_dump(mode='json'),
                success_response_types=(dict,),
            )
        except A2AError as e:
            return build_error_response(request_id=request.id, error=e)
        except Exception:
            return build_error_response(request_id=request.id, error=InternalError())

    async def on_get_contexts_list(
            self,
            request: GetContextsListRequest,
            context: Optional[ServerCallContext] = None,
    ) -> dict[str, Any]:
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
                response=context_ids.model_dump(mode='json'),
                success_response_types=(dict,),
            )
        except A2AError as e:
            return build_error_response(request_id=request.id, error=e)
        except Exception:
            return build_error_response(request_id=request.id, error=InternalError())
