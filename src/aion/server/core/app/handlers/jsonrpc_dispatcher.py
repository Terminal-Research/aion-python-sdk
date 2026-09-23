"""JSON-RPC transport binding for Aion's A2A method extensions."""

import logging
from collections.abc import AsyncGenerator
from typing import Any, override

from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
)
from a2a.server.request_handlers import prepare_response_object
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.utils.errors import UnsupportedOperationError
from aion.core.a2a import AION_JSONRPC_METHOD_EXTENSION_BINDINGS
from aion.core.runtime import aion_a2a_extension_registry
from jsonrpc.jsonrpc2 import JSONRPC20Request
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .request_handler import AionRequestHandler

logger = logging.getLogger(__name__)

_SSE_LINE_SEPARATOR = '\n'


class AionJsonRpcDispatcher(JsonRpcDispatcher):
    """The JSON-RPC binding for Aion's A2A method extensions.

    A method extension adds an RPC method of its own, and some transport has
    to know which method name carries it. This dispatcher is that knowledge
    for JSON-RPC: it intercepts exactly the methods named in
    AION_JSONRPC_METHOD_EXTENSION_BINDINGS, parses their params with the
    Pydantic model the binding names, and hands them to the request handler.

    Everything else - every standard A2A method, and anything unrecognized -
    goes straight to the parent dispatcher, which keeps its own routing and
    its own errors. That boundary is deliberate: the installed a2a-sdk has no
    public registration hook for extension methods, so the alternative to
    intercepting a known few would be reimplementing upstream routing here
    and owning it forever.

    Nothing here decides whether an extension is enabled or advertised. A
    binding carries a method name, a params model, a handler and the URI of
    the extension that defines it; activation, availability and Agent Card
    exposure live on that extension's descriptor in
    AionA2AExtensionRegistry, which is the only place they are decided - and
    this dispatcher asks it, on every call, whether the extension behind the
    method can be served at all.

    Two different questions, on two different clocks:

    - **wiring**, checked once when the dispatcher is built: does each bound
      URI have a descriptor, and does each named handler exist? A wrong
      answer is a programming mistake that no request can fix.
    - **state**, checked on every request: is that descriptor enabled,
      available, and are the extensions it requires in the same position?
      A deployment can change any of these while the process runs.
    """
    request_handler: AionRequestHandler

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build the dispatcher, validating how the method extensions are wired.

        Every bound URI must have a descriptor and every named handler must
        exist. Both are facts about the code rather than about a deployment,
        so they are settled once: a method whose URI no descriptor claims has
        an exposure policy governing nothing and would answer calls anyway,
        and one naming a handler that does not exist would fail on a caller's
        request instead of on startup. Whether the extension may be served
        right now is a different question, asked per request.
        """
        super().__init__(*args, **kwargs)
        registered = {d.uri for d in aion_a2a_extension_registry.get_all()}
        for method, binding in AION_JSONRPC_METHOD_EXTENSION_BINDINGS.items():
            if binding.extension_uri not in registered:
                raise ValueError(
                    f"JSON-RPC method '{method}' is bound to unregistered extension "
                    f"'{binding.extension_uri}' - register a descriptor for it in "
                    f"AionA2AExtensionRegistry, or remove the binding"
                )
            if not callable(getattr(self.request_handler, binding.handler_name, None)):
                raise ValueError(
                    f"JSON-RPC method '{method}' is bound to handler "
                    f"'{binding.handler_name}', which "
                    f"{type(self.request_handler).__name__} does not have"
                )

    @override
    async def handle_requests(self, request: Request) -> Response:
        try:
            body = await request.json()
        except Exception as e:
            return self._generate_error_response(None, JSONParseError(message=str(e)))

        method = body.get('method') if isinstance(body, dict) else None
        if method in AION_JSONRPC_METHOD_EXTENSION_BINDINGS:
            return await self._handle_method_extension(body, request)

        return await super().handle_requests(request)

    @override
    def _create_response(
        self,
        context: ServerCallContext,
        handler_result: AsyncGenerator[dict[str, Any], None] | dict[str, Any],
    ) -> Response:
        """Create a response with tunnel-safe SSE event delimiters.

        LF is a valid SSE line separator and keeps the required blank line
        between events independent from HTTP/1.1 CRLF transfer framing.

        Args:
            context: Server context associated with the JSON-RPC request.
            handler_result: Streaming or unary result produced by the handler.

        Returns:
            JSON or SSE response created by the upstream A2A dispatcher.
        """
        response = super()._create_response(context, handler_result)
        if isinstance(response, EventSourceResponse):
            response.sep = _SSE_LINE_SEPARATOR
        return response

    async def _handle_method_extension(self, body: dict, request: Request) -> Response:
        """Validate, parse, and dispatch a call to an Aion method extension."""
        request_id = body.get('id')
        method = body.get('method')
        binding = AION_JSONRPC_METHOD_EXTENSION_BINDINGS[method]

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

        # Deployment-level readiness, asked of the registry on every call: an
        # extension disabled for this agent, unavailable on this deployment,
        # or missing something in its requirement chain cannot answer. That
        # is the whole question here, because a method extension is invoked
        # directly and carries no A2A-Extensions declaration - there is no
        # per-request co-activation to check, which is the extra thing
        # _verify() holds a message-declared extension to. Advertisement is
        # not among these grounds: it decides what a client is told, never
        # what it may call.
        unservable = aion_a2a_extension_registry.unservable_reason(binding.extension_uri)
        if unservable is not None:
            return self._generate_error_response(
                request_id,
                UnsupportedOperationError(
                    message=f"Method {method} is not available on this agent: {unservable}"
                ),
            )

        # Parse Pydantic params — mirrors SDK's ParseDict(params, model_class())
        try:
            params = body.get('params', {})
            params_obj = binding.params_model.model_validate(params)
        except ValidationError as e:
            return self._generate_error_response(request_id, InvalidParamsError(data=str(e)))

        # Build call context (reuses parent's context builder)
        context = self._context_builder.build(request)
        context.state['method'] = method
        context.state['request_id'] = request_id
        # The extension this call belongs to travels with it: the method name
        # is a transport detail, the URI is the identity everything else -
        # logging, diagnostics, exposure policy - is stated against.
        context.state['extension_uri'] = binding.extension_uri

        try:
            handler = getattr(self.request_handler, binding.handler_name)
            result = await handler(params_obj, context)
            response_dict = prepare_response_object(
                request_id=request_id,
                response=result.model_dump(mode='json'),
                # A JSON-RPC result is any JSON value, not only an object:
                # GetContext answers with a Conversation and GetContexts with
                # an array of context ids, and refusing the array would turn
                # every call of it into InvalidAgentResponse.
                success_response_types=(dict, list),
            )
        except Exception:
            logger.error('Unhandled exception in Aion handler', exc_info=True)
            from a2a.server.jsonrpc_models import InternalError
            return self._generate_error_response(request_id, InternalError())

        return JSONResponse(response_dict)


__all__ = ['AionJsonRpcDispatcher']
