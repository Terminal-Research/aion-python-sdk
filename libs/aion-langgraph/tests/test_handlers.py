import pytest
from unittest.mock import MagicMock, Mock, patch

from langgraph.constants import START

from aion.langgraph.handlers import (
    _HandlerInvoker,
    AionEventHandlers,
    AION_ROUTER_NODE_NAME,
    add_event_handlers,
)
from aion.core.runtime.context.models import EventKind

from tests.helpers import (
    make_mock_context,
    make_mock_event,
    make_mock_inbox,
    make_mock_runtime,
)


class TestHandlerInvoker:
    async def test_sync_handler_invoked(self):
        # sync handler is transparently awaited and returns its value
        def handler(state):
            return f"got:{state}"

        invoker = _HandlerInvoker(handler)
        result = await invoker.invoke("my_state")
        assert result == "got:my_state"

    async def test_async_handler_invoked(self):
        # async handler is called and awaited correctly
        async def handler(state):
            return f"async:{state}"

        invoker = _HandlerInvoker(handler)
        result = await invoker.invoke("val")
        assert result == "async:val"

    async def test_only_declared_params_injected(self):
        # extra kwargs not declared by the handler are silently dropped
        received = {}

        async def handler(state):
            received["state"] = state

        invoker = _HandlerInvoker(handler)
        await invoker.invoke("my_state", runtime=Mock(), config=Mock())

        assert received["state"] == "my_state"
        assert "runtime" not in received
        assert "config" not in received

    async def test_langgraph_native_param_forwarded(self):
        # params not in _CONTEXT_PARAM_NAMES (e.g. config) are passed through
        received = {}

        async def handler(state, config):
            received["config"] = config

        invoker = _HandlerInvoker(handler)
        cfg = {"configurable": {}}
        await invoker.invoke("state", config=cfg)

        assert received["config"] is cfg

    async def test_context_param_triggers_runtime_resolution(self):
        # handler declaring 'context' receives it extracted from runtime
        received = {}

        async def handler(context):
            received["context"] = context

        mock_ctx = make_mock_context(event=None)
        mock_runtime = make_mock_runtime(mock_ctx)

        invoker = _HandlerInvoker(handler)
        await invoker.invoke({}, runtime=mock_runtime)

        assert received["context"] is mock_ctx

    async def test_no_context_params_skips_build(self):
        # _build_context_dependencies is never called when handler only needs state
        async def handler(state):
            return state

        invoker = _HandlerInvoker(handler)
        assert invoker._needs_runtime is False

        with patch("aion.langgraph.handlers._build_context_dependencies") as mock_build:
            await invoker.invoke("state")
            mock_build.assert_not_called()

    async def test_thread_param_injected(self):
        # handler declaring 'thread' receives a Thread instance built from context
        from aion.langgraph.runtime.context.thread import Thread

        received = {}

        async def handler(thread):
            received["thread"] = thread

        mock_ctx = make_mock_context(event=None)
        mock_runtime = make_mock_runtime(mock_ctx)

        invoker = _HandlerInvoker(handler)
        await invoker.invoke({}, runtime=mock_runtime)

        assert isinstance(received["thread"], Thread)


class TestSelectHandler:
    def test_context_none_returns_fallback(self):
        # no context (no runtime) → on_event fallback is returned
        def on_event(state): pass

        dispatcher = AionEventHandlers(on_event=on_event)
        assert dispatcher._select_handler(None) is on_event

    def test_context_none_no_fallback_returns_none(self):
        # no context and no fallback handler → None
        dispatcher = AionEventHandlers()
        assert dispatcher._select_handler(None) is None

    def test_routes_message_kind(self):
        # EventKind.MESSAGE routes to on_message
        def on_message(state): pass

        dispatcher = AionEventHandlers(on_message=on_message)
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.MESSAGE))
        assert dispatcher._select_handler(ctx) is on_message

    def test_routes_reaction_kind(self):
        # EventKind.REACTION routes to on_reaction
        def on_reaction(state): pass

        dispatcher = AionEventHandlers(on_reaction=on_reaction)
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.REACTION))
        assert dispatcher._select_handler(ctx) is on_reaction

    def test_routes_command_kind(self):
        # EventKind.COMMAND routes to on_command
        def on_command(state): pass

        dispatcher = AionEventHandlers(on_command=on_command)
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.COMMAND))
        assert dispatcher._select_handler(ctx) is on_command

    def test_routes_card_action_kind(self):
        # EventKind.CARD_ACTION routes to on_card_action
        def on_card_action(state): pass

        dispatcher = AionEventHandlers(on_card_action=on_card_action)
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.CARD_ACTION))
        assert dispatcher._select_handler(ctx) is on_card_action

    def test_unregistered_kind_falls_back_to_on_event(self):
        # event kind with no specific handler falls back to on_event
        def on_event(state): pass

        dispatcher = AionEventHandlers(on_event=on_event)
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.REACTION))
        assert dispatcher._select_handler(ctx) is on_event

    def test_no_event_with_inbox_message_routes_to_on_message(self):
        # direct A2A inbox message (no CloudEvents envelope) routes to on_message
        def on_message(state): pass

        inbox = make_mock_inbox(message=Mock())
        ctx = make_mock_context(event=None, inbox=inbox)
        dispatcher = AionEventHandlers(on_message=on_message)
        assert dispatcher._select_handler(ctx) is on_message

    def test_no_event_no_inbox_message_returns_fallback(self):
        # no event and no inbox message → on_event fallback
        def on_event(state): pass

        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=None))
        dispatcher = AionEventHandlers(on_event=on_event)
        assert dispatcher._select_handler(ctx) is on_event

    def test_no_handlers_and_unknown_kind_returns_none(self):
        # dispatcher with no handlers registered → None for any kind
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.MESSAGE))
        dispatcher = AionEventHandlers()
        assert dispatcher._select_handler(ctx) is None


class TestAionEventHandlersCall:
    async def test_routes_and_returns_handler_result(self):
        # __call__ dispatches to the right handler and returns its result
        async def on_message(state):
            return {"routed": True}

        dispatcher = AionEventHandlers(on_message=on_message)
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.MESSAGE, payload=None))
        runtime = make_mock_runtime(ctx)

        result = await dispatcher({"input": "data"}, runtime=runtime)
        assert result == {"routed": True}

    async def test_returns_none_and_warns_when_no_handler(self):
        # no matching handler → warning logged, None returned
        dispatcher = AionEventHandlers()
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.MESSAGE, payload=None))
        runtime = make_mock_runtime(ctx)

        with patch("aion.langgraph.handlers.logger") as mock_logger:
            result = await dispatcher({}, runtime=runtime)

        assert result is None
        mock_logger.warning.assert_called_once()

    async def test_works_without_runtime(self):
        # runtime kwarg absent → context=None → fallback handler is used
        def on_event(state):
            return "fallback_result"

        dispatcher = AionEventHandlers(on_event=on_event)
        result = await dispatcher({})
        assert result == "fallback_result"

    async def test_fallback_handler_called_for_unknown_event(self):
        # event kind not explicitly registered → on_event is called, not on_message
        results = []

        async def on_event(state):
            results.append("on_event")

        async def on_message(state):
            results.append("on_message")

        dispatcher = AionEventHandlers(on_message=on_message, on_event=on_event)
        ctx = make_mock_context(event=make_mock_event(kind=EventKind.REACTION, payload=None))
        runtime = make_mock_runtime(ctx)

        await dispatcher({}, runtime=runtime)
        assert results == ["on_event"]


class TestBuildSignature:
    def test_always_includes_state(self):
        # state is always the first param regardless of handlers
        sig = AionEventHandlers._build_signature([])
        assert "state" in sig.parameters

    def test_adds_runtime_when_handler_needs_context(self):
        # handler requesting 'thread' triggers runtime injection
        def handler(thread): pass

        sig = AionEventHandlers._build_signature([handler])
        assert "runtime" in sig.parameters

    def test_no_runtime_when_handler_only_needs_state(self):
        # pure state handler doesn't require runtime in signature
        def handler(state): pass

        sig = AionEventHandlers._build_signature([handler])
        assert "runtime" not in sig.parameters

    def test_forwards_langgraph_native_params(self):
        # unknown params (e.g. config) are forwarded to LangGraph for native injection
        def handler(state, config): pass

        sig = AionEventHandlers._build_signature([handler])
        assert "config" in sig.parameters

    def test_deduplicates_params_across_handlers(self):
        # same param declared by multiple handlers appears only once
        def h1(state, config): pass
        def h2(state, config, store): pass

        sig = AionEventHandlers._build_signature([h1, h2])
        params = list(sig.parameters.keys())
        assert params.count("config") == 1
        assert "store" in params

    def test_context_param_names_not_forwarded(self):
        # thread/message/identity are resolved internally, not forwarded to LangGraph
        def handler(thread, message, identity): pass

        sig = AionEventHandlers._build_signature([handler])
        assert "thread" not in sig.parameters
        assert "message" not in sig.parameters
        assert "identity" not in sig.parameters
        assert "runtime" in sig.parameters

    def test_runtime_deduplication_across_handlers(self):
        # multiple handlers needing context → runtime appears exactly once
        def h1(thread): pass
        def h2(context): pass

        sig = AionEventHandlers._build_signature([h1, h2])
        params = list(sig.parameters.keys())
        assert params.count("runtime") == 1


class TestAttach:
    def test_attach_registers_node_and_edge(self):
        # attach() wires the dispatcher into the graph as a node connected from START
        def on_message(state): pass

        dispatcher = AionEventHandlers(on_message=on_message)
        mock_builder = Mock()
        dispatcher.attach(mock_builder)

        mock_builder.add_node.assert_called_once_with(AION_ROUTER_NODE_NAME, dispatcher)
        mock_builder.add_edge.assert_called_once_with(START, AION_ROUTER_NODE_NAME)


class TestAddEventHandlers:
    def test_creates_and_attaches_dispatcher(self):
        # convenience function creates AionEventHandlers and calls attach()
        def on_message(state): pass

        mock_builder = Mock()
        add_event_handlers(mock_builder, on_message=on_message)

        mock_builder.add_node.assert_called_once()
        node_name, node_obj = mock_builder.add_node.call_args[0]
        assert node_name == AION_ROUTER_NODE_NAME
        assert isinstance(node_obj, AionEventHandlers)
        mock_builder.add_edge.assert_called_once_with(START, AION_ROUTER_NODE_NAME)

