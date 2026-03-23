import json
import logging
from _contextvars import ContextVar
from typing import Any, override

from a2a.server.apps import A2AFastAPIApplication
from a2a.server.apps.jsonrpc.jsonrpc_app import INTERNAL_ERROR_CODE
from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONRPCError,
    JSONParseError,
)
from a2a.server.request_handlers import build_error_response
from a2a.types import (
    InternalError,
    InvalidRequestError,
    UnsupportedOperationError,
)
from a2a.utils.errors import A2AError
from aion.shared.agent import AionAgent
from aion.shared.logging import get_logger
from aion.shared.types import (
    GetContextRequest,
    GetContextsListRequest,
)
from fastapi import Request, Response
from jsonrpc.jsonrpc2 import JSONRPC20Request
from pydantic import ValidationError
from starlette.responses import JSONResponse

from aion.server.constants import A2A_VERSION_DEFAULT
from aion.server.core.app.preprocessors import A2ARequestPreprocessor
from aion.server.core.request_handlers import AionJSONRPCHandler
from .api import AionExtraHTTPRoutes

# Holds the resolved A2A-Version for the current request so _create_response
# (called by parent-class methods without a Request object) can read it.
_request_a2a_version: ContextVar[str] = ContextVar("a2a_version", default=A2A_VERSION_DEFAULT)

logger = get_logger()


class AionA2AFastAPIApplication(A2AFastAPIApplication):
    """Extended A2A FastAPI application with custom request handling capabilities."""

    # Custom Aion JSON-RPC methods: method name → Pydantic model.
    # Extend this dict to add new methods without touching _handle_requests.
    AION_METHOD_TO_MODEL: dict[str, type] = {
        'context/get': GetContextRequest,
        'contexts/get': GetContextsListRequest,
    }

    @override
    def __init__(
            self,
            aion_agent: AionAgent,
            preprocessors: list[A2ARequestPreprocessor] | None = None,
            **kwargs,
    ):
        """Initialize the Aion A2A application with custom handler.

        Args:
            aion_agent: AionAgent instance.
            preprocessors: Optional list of request preprocessors applied before
                           handler routing. Executed in order for every request,
                           for both standard A2A and custom Aion methods.
            **kwargs: Forwarded to A2AFastAPIApplication.__init__
                      (http_handler, extended_agent_card, context_builder,
                       card_modifier, extended_card_modifier, max_content_length, …).
                      ``agent_card`` is always sourced from ``aion_agent.card``
                      and cannot be overridden via kwargs.
        """
        self.aion_agent = aion_agent
        self._preprocessors: list[A2ARequestPreprocessor] = preprocessors or []

        # agent_card is always sourced from aion_agent.
        kwargs['agent_card'] = self.aion_agent.card

        super().__init__(**kwargs)

        self.handler = AionJSONRPCHandler.from_existing(self.handler)

    @override
    def add_routes_to_app(self, app, **kwargs) -> None:
        """Add custom routes to the FastAPI application."""
        super().add_routes_to_app(**kwargs)
        AionExtraHTTPRoutes(self.aion_agent).register(app)

    @override
    async def _handle_requests(self, request: Request) -> Response:
        """Intercept custom Aion JSON-RPC methods before delegating to the parent."""
        try:
            body = await request.json()
        except json.decoder.JSONDecodeError as e:
            return self._generate_error_response(
                None, JSONParseError(message=str(e))
            )

        method = body.get('method') if isinstance(body, dict) else None
        if method and method in self.AION_METHOD_TO_MODEL:
            return await self._handle_aion_request(body, request)

        return await super()._handle_requests(request)

    @override
    async def _process_non_streaming_request(
            self,
            request_id: str | int | None,
            request_obj: Any,
            context: ServerCallContext,
    ) -> Response:
        await self._run_preprocessors(request_obj)
        return await super()._process_non_streaming_request(request_id, request_obj, context)

    @override
    async def _process_streaming_request(
            self,
            request_id: str | int | None,
            request_obj: Any,
            context: ServerCallContext,
    ) -> Response:
        await self._run_preprocessors(request_obj)
        return await super()._process_streaming_request(request_id, request_obj, context)

    @override
    def _generate_error_response(
            self,
            request_id: str | int | None,
            error: Exception | JSONRPCError | A2AError,
    ) -> JSONResponse:
        """Creates a Starlette JSONResponse for a JSON-RPC error.

        Logs the error based on its type.

        Args:
            request_id: The ID of the request that caused the error.
            error: The error object (one of the JSONRPCError types).

        Returns:
            A `JSONResponse` object formatted as a JSON-RPC error response.
        """
        if not isinstance(error, A2AError | JSONRPCError):
            error = InternalError(message=str(error))

        response_data = build_error_response(request_id, error)
        error_info = response_data.get('error', {})
        code = error_info.get('code')
        message = error_info.get('message')
        data = error_info.get('data')

        log_level = logging.WARNING
        if code == INTERNAL_ERROR_CODE:
            log_level = logging.ERROR

        logger.log(
            log_level,
            "Request Error (ID: %s): Code=%s, Message='%s'%s",
            request_id,
            code,
            message,
            f', Data={data}' if data else '',
        )
        return JSONResponse(
            response_data,
            status_code=200,
        )

    async def _run_preprocessors(self, request_obj: Any) -> None:
        """Run all registered preprocessors against the parsed request object."""
        for preprocessor in self._preprocessors:
            await preprocessor.preprocess(request_obj)

    async def _handle_aion_request(self, body: dict, request: Request) -> Response:
        """Validate, parse and dispatch a custom Aion JSON-RPC method.

        Mirrors parent's _handle_requests responsibilities: content-length guard,
        JSON-RPC 2.0 structure check, Pydantic parsing, context construction,
        and preprocessor execution — then delegates to _process_aion_request.
        """
        request_id = body.get('id')
        method = body.get('method')
        model_class = self.AION_METHOD_TO_MODEL[method]

        if not self._allowed_content_length(request):
            return self._generate_error_response(
                request_id,
                InvalidRequestError(message='Payload too large'),
            )

        logger.debug('Request body: %s', body)

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
            return self._generate_error_response(
                request_id,
                InvalidRequestError(data=str(e)),
            )

        try:
            request_obj = model_class.model_validate(body)
        except ValidationError as e:
            return self._generate_error_response(
                request_id,
                InvalidParamsError(data=str(e)),
            )

        call_context = self._context_builder.build(request)
        call_context.tenant = getattr(request_obj, 'tenant', '')
        call_context.state['method'] = method
        call_context.state['request_id'] = request_id

        return await self._process_aion_request(request_id, request_obj, call_context)

    async def _process_aion_request(
            self,
            request_id: str | int | None,
            request_obj: Any,
            context: ServerCallContext,
    ) -> Response:
        """Route a parsed Aion request to the appropriate handler method."""
        await self._run_preprocessors(request_obj)

        try:
            match request_obj:
                case GetContextRequest():
                    handler_result = await self.handler.on_get_context(request_obj, context)
                case GetContextsListRequest():
                    handler_result = await self.handler.on_get_contexts_list(request_obj, context)
                case _:
                    logger.error(
                        'Unhandled validated request type: %s', type(request_obj)
                    )
                    error = UnsupportedOperationError(
                        message=f'Request type {type(request_obj).__name__} is unknown.'
                    )
                    return self._generate_error_response(request_id, error)

        except Exception:
            logger.error('Unhandled exception in Aion handler', exc_info=True)
            return self._generate_error_response(request_id, InternalError())

        return self._create_response(context, handler_result)


__all__ = ["AionA2AFastAPIApplication"]
