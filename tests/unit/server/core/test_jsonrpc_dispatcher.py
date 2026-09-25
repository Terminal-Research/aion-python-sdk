"""Wire-format and routing tests for the Aion JSON-RPC dispatcher."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from sse_starlette.sse import EventSourceResponse
from starlette.responses import JSONResponse, Response

from a2a.types import TaskState

from aion.core.constants.a2a import GET_CONTEXTS_LIST_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry
from aion.core.runtime.context.extensions.descriptors import ExtensionDescriptor
from aion.core.a2a import (
    AION_JSONRPC_METHOD_EXTENSION_BINDINGS,
    AionJsonRpcMethodExtensionBinding,
    ContextsList,
    Conversation,
    ConversationTaskStatus,
)
from aion.server.core.app.handlers import jsonrpc_dispatcher as dispatcher_module
from aion.server.core.app.handlers.jsonrpc_dispatcher import (
    AionJsonRpcDispatcher,
)


@pytest.mark.asyncio
async def test_streaming_response_uses_lf_event_delimiters() -> None:
    """Keep consecutive JSON-RPC events distinct through HTTP tunnels."""

    async def results() -> AsyncGenerator[dict[str, Any], None]:
        yield {'jsonrpc': '2.0', 'id': 1, 'result': {'sequence': 1}}
        yield {'jsonrpc': '2.0', 'id': 1, 'result': {'sequence': 2}}

    dispatcher = AionJsonRpcDispatcher(request_handler=Mock())
    response = dispatcher._create_response(Mock(), results())
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    async def receive() -> dict[str, Any]:
        # The client never disconnects; the response ends with its stream.
        await asyncio.Event().wait()
        return {'type': 'http.disconnect'}

    assert isinstance(response, EventSourceResponse)
    # Through the public ASGI entry point rather than a private helper of
    # sse-starlette, whose name is not stable across its releases.
    await response({'type': 'http', 'method': 'POST', 'headers': []}, receive, send)

    body = b''.join(
        message.get('body', b'')
        for message in messages
        if message['type'] == 'http.response.body'
    )
    assert b'\r' not in body
    assert body.count(b'\n\ndata: ') == 1
    assert body.endswith(b'\n\n')


def _request(body: dict) -> Mock:
    """A Starlette request thin enough for the routing decision under test."""
    request = Mock()
    request.json = AsyncMock(return_value=body)
    return request


def _conversation() -> Conversation:
    return Conversation(
        context_id="c1",
        status=ConversationTaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )


def _dispatcher(handler: Mock) -> AionJsonRpcDispatcher:
    dispatcher = AionJsonRpcDispatcher(request_handler=handler)
    context = Mock()
    context.state = {}
    dispatcher._context_builder = Mock()
    dispatcher._context_builder.build = Mock(return_value=context)
    return dispatcher


class TestMethodExtensionRouting:
    """Which methods this dispatcher claims, and which it must not."""

    @pytest.mark.asyncio
    async def test_a_bound_method_is_parsed_with_the_binding_s_model(self, monkeypatch):
        """The params model comes from the binding, not from a second table.

        Asserted by replacing the binding's model and watching the parse
        follow it: with a duplicate mapping still in the dispatcher, this is
        exactly what would keep using the old one.
        """
        seen: dict[str, Any] = {}

        bound = AION_JSONRPC_METHOD_EXTENSION_BINDINGS["GetContext"]

        class _RecordingParams(bound.params_model):
            @classmethod
            def model_validate(cls, value, *args, **kwargs):
                seen["params"] = value
                return super().model_validate(value, *args, **kwargs)

        monkeypatch.setattr(
            dispatcher_module,
            "AION_JSONRPC_METHOD_EXTENSION_BINDINGS",
            {
                "GetContext": AionJsonRpcMethodExtensionBinding(
                    extension_uri=bound.extension_uri,
                    params_model=_RecordingParams,
                    handler_name=bound.handler_name,
                )
            },
        )

        handler = Mock()
        handler.on_get_context = AsyncMock(return_value=_conversation())
        dispatcher = _dispatcher(handler)

        response = await dispatcher.handle_requests(
            _request(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "GetContext",
                    "params": {"context_id": "c1"},
                }
            )
        )

        assert seen["params"] == {"context_id": "c1"}
        assert isinstance(response, JSONResponse)
        assert json.loads(response.body)["result"]["contextId"] == "c1"
        handler.on_get_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_context_answers_with_its_conversation(self):
        """The whole wire path for GetContext, body included.

        Asserting the response body rather than that the handler ran: the
        handler running and the caller receiving an error are exactly the
        combination a handler-only assertion cannot tell apart.
        """
        handler = Mock()
        handler.on_get_context = AsyncMock(return_value=_conversation())
        dispatcher = _dispatcher(handler)

        response = await dispatcher.handle_requests(
            _request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "GetContext",
                    "params": {"context_id": "c1"},
                }
            )
        )

        assert isinstance(response, JSONResponse)
        body = json.loads(response.body)
        assert "error" not in body
        assert body["id"] == 1
        assert body["result"]["contextId"] == "c1"
        handler.on_get_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_contexts_answers_with_its_array_of_ids(self):
        """The same for GetContexts, whose result is an array rather than an
        object - which is the whole reason it needs its own wire test."""
        handler = Mock()
        handler.on_get_contexts_list = AsyncMock(return_value=ContextsList(["c1", "c2"]))
        dispatcher = _dispatcher(handler)

        response = await dispatcher.handle_requests(
            _request({"jsonrpc": "2.0", "id": 2, "method": "GetContexts", "params": {}})
        )

        assert isinstance(response, JSONResponse)
        body = json.loads(response.body)
        assert "error" not in body
        assert body["id"] == 2
        assert body["result"] == ["c1", "c2"]
        handler.on_get_contexts_list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_handler_is_resolved_from_the_binding(self):
        """Routing reads handler_name; nothing in the dispatcher names the
        two methods it happens to serve."""
        bound = AION_JSONRPC_METHOD_EXTENSION_BINDINGS["GetContext"]
        handler = Mock()
        handler.answer_somewhere_else = AsyncMock(return_value=_conversation())
        monkey = {
            "GetContext": AionJsonRpcMethodExtensionBinding(
                extension_uri=bound.extension_uri,
                params_model=bound.params_model,
                handler_name="answer_somewhere_else",
            )
        }
        dispatcher = _dispatcher(handler)
        dispatcher_module.AION_JSONRPC_METHOD_EXTENSION_BINDINGS = monkey
        try:
            response = await dispatcher.handle_requests(
                _request(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "GetContext",
                        "params": {"context_id": "c1"},
                    }
                )
            )
        finally:
            dispatcher_module.AION_JSONRPC_METHOD_EXTENSION_BINDINGS = (
                AION_JSONRPC_METHOD_EXTENSION_BINDINGS
            )

        assert isinstance(response, JSONResponse)
        assert "error" not in json.loads(response.body)
        handler.answer_somewhere_else.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_extension_uri_travels_on_the_call_context(self):
        """The identity the method belongs to reaches everything downstream,
        beside the transport-level method name."""
        handler = Mock()
        handler.on_get_contexts_list = AsyncMock(return_value=ContextsList([]))
        dispatcher = _dispatcher(handler)

        await dispatcher.handle_requests(
            _request({"jsonrpc": "2.0", "id": 4, "method": "GetContexts", "params": {}})
        )

        context = handler.on_get_contexts_list.await_args.args[1]
        assert context.state["method"] == "GetContexts"
        assert (
            context.state["extension_uri"]
            == AION_JSONRPC_METHOD_EXTENSION_BINDINGS["GetContexts"].extension_uri
        )


class TestStateEnforcement:
    """What the registry says about an extension *now* decides the call.

    Separate from the wiring checks below, and on a different clock: a
    deployment can disable an extension, lose the toolkit it needs, or leave
    a requirement of it inactive while the process runs, and each of those
    has to refuse the next call rather than the next startup.
    """

    @pytest.fixture(autouse=True)
    def _registry(self, isolated_registry):
        pass

    def _rebind(self, descriptor: ExtensionDescriptor) -> None:
        aion_a2a_extension_registry.register(descriptor)

    async def _call(self) -> dict:
        handler = Mock()
        handler.on_get_contexts_list = AsyncMock(return_value=ContextsList([]))
        dispatcher = _dispatcher(handler)
        response = await dispatcher.handle_requests(
            _request({"jsonrpc": "2.0", "id": 9, "method": "GetContexts", "params": {}})
        )
        self.handler = handler
        return json.loads(response.body)

    @pytest.mark.asyncio
    async def test_a_servable_extension_answers(self):
        """The baseline the refusals below are worth nothing without."""
        body = await self._call()

        assert "error" not in body
        self.handler.on_get_contexts_list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_deactivated_extension_refuses_the_call(self):
        self._rebind(
            ExtensionDescriptor(uri=GET_CONTEXTS_LIST_EXTENSION_URI_V1, active=False)
        )

        body = await self._call()

        assert "enabled_extensions" in body["error"]["message"]
        self.handler.on_get_contexts_list.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unavailable_extension_refuses_with_its_own_reason(self):
        aion_a2a_extension_registry.mark_unavailable(
            GET_CONTEXTS_LIST_EXTENSION_URI_V1, "the context store is not configured"
        )

        body = await self._call()

        assert "the context store is not configured" in body["error"]["message"]
        self.handler.on_get_contexts_list.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_missing_requirement_refuses_the_call(self):
        """Transitive, like everywhere else: the requirement chain decides."""
        required = "aion://extensions/test-dispatcher-required/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=required, active=False)
        )
        self._rebind(
            ExtensionDescriptor(
                uri=GET_CONTEXTS_LIST_EXTENSION_URI_V1, requires=(required,)
            )
        )

        body = await self._call()

        assert required in body["error"]["message"]
        self.handler.on_get_contexts_list.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_withholding_it_from_the_card_does_not_refuse_the_call(self):
        """`advertised` is not an authorization mechanism, and this is where
        that would quietly stop being true."""
        self._rebind(
            ExtensionDescriptor(
                uri=GET_CONTEXTS_LIST_EXTENSION_URI_V1, advertised=False
            )
        )

        body = await self._call()

        assert "error" not in body

    @pytest.mark.asyncio
    async def test_advertising_it_does_not_permit_a_disabled_call(self):
        """The other direction of the same claim."""
        self._rebind(
            ExtensionDescriptor(
                uri=GET_CONTEXTS_LIST_EXTENSION_URI_V1, active=False, advertised=True
            )
        )

        body = await self._call()

        assert "error" in body


class TestWiringValidation:
    """A binding that cannot route at all stops the server.

    Facts about the code, not about a deployment, so they are settled once at
    construction: no request can make a missing descriptor appear or a
    misspelled handler exist.
    """

    def test_construction_fails_on_a_binding_with_no_descriptor(self, monkeypatch):
        bound = AION_JSONRPC_METHOD_EXTENSION_BINDINGS["GetContext"]
        monkeypatch.setattr(
            dispatcher_module,
            "AION_JSONRPC_METHOD_EXTENSION_BINDINGS",
            {
                "GetContext": AionJsonRpcMethodExtensionBinding(
                    extension_uri="aion://extensions/never-registered/v1",
                    params_model=bound.params_model,
                    handler_name=bound.handler_name,
                )
            },
        )

        with pytest.raises(ValueError, match="never-registered"):
            AionJsonRpcDispatcher(request_handler=Mock())

    def test_construction_fails_on_a_binding_naming_a_handler_that_is_absent(
        self, monkeypatch
    ):
        bound = AION_JSONRPC_METHOD_EXTENSION_BINDINGS["GetContext"]
        monkeypatch.setattr(
            dispatcher_module,
            "AION_JSONRPC_METHOD_EXTENSION_BINDINGS",
            {
                "GetContext": AionJsonRpcMethodExtensionBinding(
                    extension_uri=bound.extension_uri,
                    params_model=bound.params_model,
                    handler_name="on_get_contxet",
                )
            },
        )
        handler = Mock(spec=[])

        with pytest.raises(ValueError, match="on_get_contxet"):
            AionJsonRpcDispatcher(request_handler=handler)

    def test_construction_succeeds_on_the_shipped_bindings(self):
        assert AionJsonRpcDispatcher(request_handler=Mock()) is not None

    @pytest.mark.asyncio
    async def test_a_standard_a2a_method_is_delegated_upstream(self, monkeypatch):
        """Standard methods never enter this dispatcher's routing.

        The installed a2a-sdk owns them, and the moment we answered one here
        we would own its routing and its errors for good.
        """
        delegated = Mock(return_value=Response(status_code=204))
        monkeypatch.setattr(
            JsonRpcDispatcher, "handle_requests", AsyncMock(side_effect=delegated)
        )
        handler = Mock()
        dispatcher = _dispatcher(handler)

        await dispatcher.handle_requests(
            _request({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}})
        )

        delegated.assert_called_once()

    @pytest.mark.asyncio
    async def test_an_unknown_method_is_delegated_upstream(self, monkeypatch):
        """Keeping upstream's error for an unknown method, rather than
        inventing a second one that differs from it."""
        delegated = Mock(return_value=Response(status_code=204))
        monkeypatch.setattr(
            JsonRpcDispatcher, "handle_requests", AsyncMock(side_effect=delegated)
        )
        dispatcher = _dispatcher(Mock())

        await dispatcher.handle_requests(
            _request({"jsonrpc": "2.0", "id": 1, "method": "NoSuchMethod", "params": {}})
        )

        delegated.assert_called_once()
