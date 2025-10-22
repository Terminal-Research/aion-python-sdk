import json
import traceback
from typing import get_args

from a2a.server.apps import A2AStarletteApplication
from a2a.server.apps.jsonrpc.jsonrpc_app import (
    CallContextBuilder,
)
from a2a.types import (
    A2AError, JSONParseError, InternalError, TaskResubscriptionRequest,
    SendStreamingMessageRequest, InvalidRequestError,
    UnsupportedOperationError, JSONRPCErrorResponse, JSONRPCRequest
)
from a2a.types import AgentCard
from a2a.utils.errors import MethodNotImplementedError
from aion.shared.logging import get_logger
from opentelemetry import trace
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.status import HTTP_413_REQUEST_ENTITY_TOO_LARGE

from aion.server.core.request_handlers import AionJSONRPCHandler
from aion.server.interfaces import IRequestHandler
from aion.server.types import ExtendedA2ARequest, CustomA2ARequest, GetContextRequest, GetContextsListRequest

logger = get_logger()
request_tracer = trace.get_tracer("langgraph.agent")


class AionA2AStarletteApplication(A2AStarletteApplication):
    """Extended A2A Starlette application with custom request handling capabilities."""

    def __init__(
            self,
            agent_card: AgentCard,
            http_handler: IRequestHandler,
            extended_agent_card: AgentCard | None = None,
            context_builder: CallContextBuilder | None = None
    ):
        """Initialize the Aion A2A application with custom handler.

        Args:
            agent_card: Main agent configuration card
            http_handler: Request handler implementation
            extended_agent_card: Optional extended agent configuration
            context_builder: Optional context builder for requests
        """
        super().__init__(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder)

        # replace handler with our custom handler with additional methods
        self.handler = AionJSONRPCHandler(
            agent_card=agent_card,
            request_handler=http_handler)

    async def _handle_requests(self, request: Request) -> Response:
        """Handle incoming HTTP requests with comprehensive error handling.

        Args:
            request: Incoming HTTP request

        Returns:
            HTTP response with result or error
        """
        request_id = None
        body = None

        try:
            body = await request.json()
            if isinstance(body, dict):
                request_id = body.get('id')

            # First, validate the basic JSON-RPC structure. This is crucial
            # because the A2ARequest model is a discriminated union where some
            # request types have default values for the 'method' field
            JSONRPCRequest.model_validate(body)

            a2a_request = ExtendedA2ARequest.model_validate(body)
            call_context = self._context_builder.build(request)

            request_id = a2a_request.root.id
            request_obj = a2a_request.root

            if self._check_if_request_is_custom_method(request_obj):
                return await self._handle_custom_requests(
                    request, a2a_request, call_context
                )

            # default a2a-sdk processing
            if isinstance(
                    request_obj,
                    TaskResubscriptionRequest | SendStreamingMessageRequest,
            ):
                return await self._process_streaming_request(
                    request_id, a2a_request, call_context
                )

            return await self._process_non_streaming_request(
                request_id, a2a_request, call_context
            )
        except MethodNotImplementedError:
            traceback.print_exc()
            return self._generate_error_response(
                request_id, A2AError(root=UnsupportedOperationError())
            )
        except json.decoder.JSONDecodeError as e:
            traceback.print_exc()
            return self._generate_error_response(
                None, A2AError(root=JSONParseError(message=str(e)))
            )
        except ValidationError as e:
            traceback.print_exc()
            return self._generate_error_response(
                request_id,
                A2AError(root=InvalidRequestError(data=json.loads(e.json()))),
            )
        except HTTPException as e:
            if e.status_code == HTTP_413_REQUEST_ENTITY_TOO_LARGE:
                return self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidRequestError(message='Payload too large')
                    ),
                )
            raise e
        except Exception as e:
            logger.error(f'Unhandled exception: {e}')
            traceback.print_exc()
            return self._generate_error_response(
                request_id, A2AError(root=InternalError(message=str(e)))
            )

    async def _handle_custom_requests(self, request: Request, a2a_request, context) -> Response:
        """Handle custom request types specific to Aion implementation.

        Args:
            request: Original HTTP request
            a2a_request: Validated A2A request object
            context: Call context for the request

        Returns:
            HTTP response with handler result
        """
        request_obj = a2a_request.root
        request_id = request_obj.id

        match request_obj:
            case GetContextRequest():
                handler_result = (
                    await self.handler.on_get_context(
                        request_obj, context
                    )
                )

            case GetContextsListRequest():
                handler_result = (
                    await self.handler.on_get_contexts_list(
                        request_obj, context
                    )
                )

            case _:
                logger.error(
                    f'Unhandled validated request type: {type(request_obj)}'
                )
                error = UnsupportedOperationError(
                    message=f'Request type {type(request_obj).__name__} is unknown.'
                )
                handler_result = JSONRPCErrorResponse(
                    id=request_id, error=error
                )

        return self._create_response(context=context, handler_result=handler_result)

    def _check_if_request_is_custom_method(self, request_obj):
        """Check if the request is a custom method type.

        Args:
            request_obj: Request object to check

        Returns:
            True if request is custom method, False otherwise
        """
        custom_types = get_args(CustomA2ARequest.model_fields['root'].annotation)
        return type(request_obj) in custom_types


__all__ = ["AionA2AStarletteApplication"]
