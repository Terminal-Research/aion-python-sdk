"""a2a-sdk's A2A 0.3 JSON-RPC adapter, with the stream rules of the 1.0 binding."""

from typing import Any, override

from a2a.compat.v0_3.conversions import to_core_send_message_request
from a2a.compat.v0_3.jsonrpc_adapter import JSONRPC03Adapter
from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import InvalidParamsError as JSONRPCInvalidParamsError
from a2a.utils import constants
from a2a.utils.errors import InvalidParamsError
from a2a.utils.version_validator import validate_version
from sse_starlette.sse import EventSourceResponse
from starlette.responses import JSONResponse

from .sse import SSE_LINE_SEPARATOR


class AionJSONRPC03Adapter(JSONRPC03Adapter):
    """Serves A2A 0.3's methods as a2a-sdk does, and opens its streams as the 1.0 binding does.

    a2a-sdk's adapter builds the SSE response of ``message/stream`` and
    ``tasks/resubscribe`` itself, so ``AionJsonRpcDispatcher._create_response``
    never sees it. Two of the dispatcher's rules are therefore repeated here:

    - the events are separated with ``SSE_LINE_SEPARATOR``;
    - a ``message/stream`` whose declared extensions fail verification is
      refused with a plain JSON-RPC response, ``-32602``, before the stream
      opens, rather than with an error event inside it.
    """

    @override
    @validate_version(constants.PROTOCOL_VERSION_0_3)
    async def _process_streaming_request(
        self,
        request_id: str | int | None,
        request_obj: Any,
        context: ServerCallContext,
    ) -> EventSourceResponse | JSONResponse:
        """Verify a send's extensions, then open a2a-sdk's stream with LF separators.

        Args:
            request_id: JSON-RPC correlation ID.
            request_obj: The parsed v0.3 request.
            context: Invocation-local authentication and service parameters.

        Returns:
            The SSE response, or the JSON-RPC refusal of a send whose
            extension declarations fail verification.
        """
        if request_obj.method == 'message/stream':
            try:
                self.handler.request_handler.verify_declared_extensions(
                    to_core_send_message_request(request_obj), context
                )
            except InvalidParamsError as error:
                return self._generate_error_response(
                    request_id, JSONRPCInvalidParamsError(message=str(error))
                )
        response = await super()._process_streaming_request(request_id, request_obj, context)
        response.sep = SSE_LINE_SEPARATOR
        return response


__all__ = ['AionJSONRPC03Adapter']
