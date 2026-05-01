from typing import override

from a2a.server.jsonrpc_models import (
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
)
from a2a.server.request_handlers import prepare_response_object
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.utils.errors import UnsupportedOperationError
from aion.shared.logging import get_logger
from aion.shared.types import GetContextParams, GetContextsListParams
from jsonrpc.jsonrpc2 import JSONRPC20Request
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .request_handler import AionRequestHandler

logger = get_logger()


class AionJsonRpcDispatcher(JsonRpcDispatcher):
    """Extends JsonRpcDispatcher with Aion-specific JSON-RPC methods.

    Intercepts GetContext and GetContexts before the standard protobuf-based
    routing and handles them via Pydantic models. All standard A2A methods
    are delegated to the parent dispatcher unchanged.
    """
    request_handler: AionRequestHandler

    AION_METHOD_TO_MODEL: dict[str, type] = {
        'GetContext': GetContextParams,
        'GetContexts': GetContextsListParams,
    }

    @override
    async def handle_requests(self, request: Request) -> Response:
        try:
            body = await request.json()
        except Exception as e:
            return self._generate_error_response(None, JSONParseError(message=str(e)))

        method = body.get('method') if isinstance(body, dict) else None
        if method in self.AION_METHOD_TO_MODEL:
            return await self._handle_aion_method(body, request)

        return await super().handle_requests(request)

    async def _handle_aion_method(self, body: dict, request: Request) -> Response:
        request_id = body.get('id')
        method = body.get('method')
        model_class = self.AION_METHOD_TO_MODEL[method]

        logger.debug('Aion request body: %s', body)

        # Validate base JSON-RPC structure (reuses parent's error response)
        try:
            base_request = JSONRPC20Request.from_data(body)
            if not isinstance(base_request, JSONRPC20Request):
                return self._generate_error_response(
                    request_id,
                    InvalidRequestError(message='Batch requests are not supported'),
                )
            if body.get('jsonrpc') != '2.0':
                return self._generate_error_response(
                    request_id,
                    InvalidRequestError(message="Invalid request: 'jsonrpc' must be exactly '2.0'"),
                )
            request_id = base_request._id  # noqa: SLF001
        except Exception as e:
            logger.exception('Failed to validate base JSON-RPC request')
            return self._generate_error_response(request_id, InvalidRequestError(data=str(e)))

        # Parse Pydantic params — mirrors SDK's ParseDict(params, model_class())
        try:
            params = body.get('params', {})
            params_obj = model_class.model_validate(params)
        except ValidationError as e:
            return self._generate_error_response(request_id, InvalidParamsError(data=str(e)))

        # Build call context (reuses parent's context builder)
        context = self._context_builder.build(request)
        context.state['method'] = method
        context.state['request_id'] = request_id

        try:
            match params_obj:
                case GetContextParams():
                    result = await self.request_handler.on_get_context(params_obj, context)
                    response_dict = prepare_response_object(
                        request_id=request_id,
                        response=result.model_dump(mode='json'),
                        success_response_types=(dict,),
                    )
                case GetContextsListParams():
                    result = await self.request_handler.on_get_contexts_list(params_obj, context)
                    response_dict = prepare_response_object(
                        request_id=request_id,
                        response=result.model_dump(mode='json'),
                        success_response_types=(dict,),
                    )
                case _:
                    return self._generate_error_response(
                        request_id,
                        UnsupportedOperationError(message=f'Method {method} is unknown.'),
                    )
        except Exception:
            logger.error('Unhandled exception in Aion handler', exc_info=True)
            from a2a.server.jsonrpc_models import InternalError
            return self._generate_error_response(request_id, InternalError())

        return JSONResponse(response_dict)


__all__ = ['AionJsonRpcDispatcher']
