"""JSON-RPC transport binding for Aion's A2A method extensions."""

import logging
from collections.abc import AsyncGenerator
from typing import Any, override

from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import JSONParseError
from a2a.server.request_handlers import prepare_response_object
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.utils import constants, proto_utils
from a2a.utils.errors import (
    A2AError,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
from a2a.utils.version_validator import validate_version
from google.protobuf.json_format import MessageToDict
from aion.core.a2a import AION_JSONRPC_METHOD_EXTENSION_BINDINGS
from aion.core.runtime import aion_a2a_extension_registry
from aion.server.contexts import ContextLifecycleError
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .jsonrpc_v03_adapter import AionJSONRPC03Adapter
from .request_handler import AionRequestHandler
from .sse import SSE_LINE_SEPARATOR
from aion.server.a2a.response_extensions import ResponseServiceParameters

logger = logging.getLogger(__name__)


def _refuse_unsupported_version(context: ServerCallContext) -> None:
    """Refuse a method extension call announcing an A2A version other than 1.x.

    The standard methods' rule, ``a2a.utils.version_validator``, with one
    difference: a call that announces no version is served. a2a-sdk reads a
    missing ``A2A-Version`` as 0.3, and the method extensions have no 0.3
    form, so that rule would refuse every caller that does not send the
    header.

    Raises:
        VersionNotSupportedError: ``A2A-Version`` names another major version,
            or is not a version at all.
    """
    headers = context.state.get('headers', {})
    announced = headers.get(constants.VERSION_HEADER) or headers.get(constants.VERSION_HEADER.lower())
    if not announced:
        return
    # Major versions compared, as a2a-sdk compares them: "1", "1.0" and "1.2"
    # are all 1.x.
    expected_major = constants.PROTOCOL_VERSION_1_0.split('.', 1)[0]
    if str(announced).strip().split('.', 1)[0] != expected_major:
        raise VersionNotSupportedError(
            message=f"A2A version '{announced}' is not supported by this handler. "
            f"Expected version '{constants.PROTOCOL_VERSION_1_0}'."
        )


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

        A2A 0.3's methods are served by ``AionJSONRPC03Adapter`` in place of
        a2a-sdk's adapter, so its streams follow this binding's stream rules.
        """
        super().__init__(*args, **kwargs)
        if self._v03_adapter is not None:
            self._v03_adapter = AionJSONRPC03Adapter(
                http_handler=self.request_handler,
                context_builder=self._context_builder,
                shutdown_grace_period=self._shutdown_grace_period,
            )
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
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def _process_streaming_request(
        self,
        request_id: str | int | None,
        request_obj: Any,
        context: ServerCallContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Verify send activation before headers without pulling a body event.

        Read subscriptions keep upstream startup validation. Send validation
        of extension payloads is synchronous; subsequent execution failures
        remain JSON-RPC errors in the SSE body. Closing the response closes
        its owned generator, including on client cancellation.

        Args:
            request_id: JSON-RPC correlation ID.
            request_obj: Parsed protocol request.
            context: Invocation-local authentication and service parameters.

        Returns:
            An unconsumed generator after extension preflight succeeds.
        """
        if context.state.get('method') != 'SendStreamingMessage':
            return await super()._process_streaming_request(
                request_id, request_obj, context
            )
        self.request_handler.verify_declared_extensions(request_obj, context)

        async def results():
            stream = self.request_handler.on_message_send_stream(request_obj, context)
            try:
                async for event in stream:
                    result = MessageToDict(proto_utils.to_stream_response(event))
                    yield JSONRPC20Response(result=result, _id=request_id).data
            except A2AError as error:
                yield build_error_response(request_id, error)
            finally:
                await stream.aclose()

        return results()

    @override
    def _create_response(
        self,
        context: ServerCallContext,
        handler_result: AsyncGenerator[dict[str, Any], None] | dict[str, Any],
    ) -> Response:
        """Create a response with tunnel-safe SSE event delimiters.

        The events are separated with ``SSE_LINE_SEPARATOR``.

        Args:
            context: Server context associated with the JSON-RPC request.
            handler_result: Streaming or unary result produced by the handler.

        Returns:
            JSON or SSE response created by the upstream A2A dispatcher.
        """
        response = super()._create_response(context, handler_result)
        parameters = ResponseServiceParameters.from_context(context)
        if parameters.activated_extensions:
            response.headers['A2A-Extensions'] = ','.join(parameters.activated_extensions)
        if isinstance(response, EventSourceResponse):
            response.sep = SSE_LINE_SEPARATOR
        return response

    async def _handle_method_extension(self, body: dict, request: Request) -> Response:
        """Validate, parse, and dispatch a call to an Aion method extension."""
        request_id = body.get('id')
        method = body.get('method')
        binding = AION_JSONRPC_METHOD_EXTENSION_BINDINGS[method]

        # The method, not the body: params.metadata carries the distribution
        # payload, whose configuration variables hold secrets in plain text.
        logger.debug('Aion method extension call: %s', method)

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
            return self._generate_error_response(request_id, InvalidRequestError(data={'parseError': str(e)}))

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

        # Parse Pydantic params as the SDK parses its own: unknown fields are
        # ignored, and a failure answers -32602 with the reason in the
        # ErrorInfo detail's metadata.parseError.
        try:
            params = body.get('params', {})
            params_obj = binding.params_model.model_validate(params)
        except ValidationError as e:
            return self._generate_error_response(request_id, InvalidParamsError(data={'parseError': str(e)}))

        # Build call context (reuses parent's context builder)
        context = self._context_builder.build(request)
        context.state['method'] = method
        context.state['request_id'] = request_id
        # The extension this call belongs to travels with it: the method name
        # is a transport detail, the URI is the identity everything else -
        # logging, diagnostics, exposure policy - is stated against.
        context.state['extension_uri'] = binding.extension_uri

        try:
            _refuse_unsupported_version(context)
        except VersionNotSupportedError as error:
            return self._generate_error_response(request_id, error)

        try:
            handler = getattr(self.request_handler, binding.handler_name)
            result = await handler(params_obj, context)
            response = JSONResponse(prepare_response_object(
                request_id=request_id,
                response=result.model_dump(mode='json'),
                # A JSON-RPC result is any JSON value, not only an object:
                # GetContexts answers with a raw array of context summaries,
                # and refusing the array would turn every call of it into
                # InvalidAgentResponse.
                success_response_types=(dict, list),
            ))
        except ContextLifecycleError as error:
            # The Context extension's own codes (1000-1002) sit outside the
            # JSON-RPC server-error band and carry their own data, so they are
            # rendered here rather than through a2a-sdk's error maps.
            response = JSONResponse({'jsonrpc': '2.0', 'id': request_id, 'error': error.jsonrpc_error()})
        except Exception:
            logger.error('Unhandled exception in Aion handler', exc_info=True)
            return self._generate_error_response(request_id, InternalError())

        # The extension served the call, so the response acknowledges it as a
        # standard method's acknowledges the extensions it verified.
        response.headers['A2A-Extensions'] = binding.extension_uri
        return response


__all__ = ['AionJsonRpcDispatcher']
