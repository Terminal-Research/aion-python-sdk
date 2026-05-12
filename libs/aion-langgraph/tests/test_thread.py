import pytest
from unittest.mock import MagicMock, Mock, patch
from langchain_core.messages import AIMessage, AIMessageChunk
from a2a.types import Artifact

from aion.langgraph.runtime.context.thread import Thread
from aion.langgraph.runtime.context.message import Message
from aion.langgraph.events.custom_events import ArtifactCustomEvent, MessageCustomEvent

from tests.helpers import (
    make_mock_context,
    make_mock_event,
    make_mock_inbox,
    make_mock_identity,
    make_mock_runtime,
)


def make_thread(context=None, writer=None, **kwargs):
    ctx = context or make_mock_context()
    return Thread(
        context=ctx,
        id=kwargs.get("id", "C123"),
        parent_id=kwargs.get("parent_id", None),
        network=kwargs.get("network", "Slack"),
        default_reply_target=kwargs.get("default_reply_target", "C123"),
        writer=writer if writer is not None else MagicMock(),
    )


class TestThreadFromContext:
    def test_extracts_context_id_from_event_payload(self):
        # context_id is read from the inbound event payload
        payload = Mock()
        payload.context_id = "C-from-event"
        payload.trajectory = "conversation"
        payload.parent_context_id = None
        ctx = make_mock_context(event=make_mock_event(payload=payload))

        thread = Thread.from_context(ctx)
        assert thread.id == "C-from-event"

    def test_falls_back_to_inbox_message_context_id(self):
        # when no event payload, context_id comes from inbox.message
        inbox_msg = Mock()
        inbox_msg.context_id = "C-from-inbox"
        inbox_msg.message_id = "msg1"
        inbox_msg.parts = []
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))

        thread = Thread.from_context(ctx)
        assert thread.id == "C-from-inbox"

    def test_falls_back_to_inbox_task_context_id(self):
        # when no event and no inbox message, context_id comes from inbox.task
        task = Mock()
        task.context_id = "C-from-task"
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(task=task))

        thread = Thread.from_context(ctx)
        assert thread.id == "C-from-task"

    def test_reply_trajectory_sets_default_target_to_parent(self):
        # reply trajectory routes responses to the parent context, not the current one
        payload = Mock()
        payload.context_id = "C-current"
        payload.trajectory = "reply"
        payload.parent_context_id = "C-parent"
        ctx = make_mock_context(event=make_mock_event(payload=payload))

        thread = Thread.from_context(ctx)
        assert thread.default_reply_target == "C-parent"

    def test_conversation_trajectory_sets_default_target_to_context_id(self):
        # non-reply trajectory routes responses to the current context
        payload = Mock()
        payload.context_id = "C-current"
        payload.trajectory = "conversation"
        payload.parent_context_id = "C-parent"
        ctx = make_mock_context(event=make_mock_event(payload=payload))

        thread = Thread.from_context(ctx)
        assert thread.default_reply_target == "C-current"

    def test_network_from_identity(self):
        # network type is extracted from the agent identity
        ctx = make_mock_context(event=None, identity=make_mock_identity(network_type="Slack"))
        thread = Thread.from_context(ctx)
        assert thread.network == "Slack"

    def test_network_defaults_to_a2a_when_no_identity(self):
        # when identity is absent, network falls back to "A2A"
        ctx = make_mock_context(event=None)
        ctx.identity = None
        thread = Thread.from_context(ctx)
        assert thread.network == "A2A"

    def test_message_none_when_no_inbox_message(self):
        # thread.message is None when the inbox carries no message
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=None))
        thread = Thread.from_context(ctx)
        assert thread.message is None

    def test_message_built_when_inbox_message_present(self):
        # thread.message is a Message instance when inbox.message is set
        inbox_msg = Mock()
        inbox_msg.context_id = "C123"
        inbox_msg.message_id = "msg1"
        inbox_msg.parts = []
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))

        thread = Thread.from_context(ctx)
        assert isinstance(thread.message, Message)


class TestDetectJsxCard:
    def test_detects_card_markup(self):
        # string starting with <Card is recognized as JSX card markup
        assert Thread._detect_jsx_card_markup("<Card>hello</Card>") is True

    def test_rejects_non_card_content(self):
        # plain text and other tags are not treated as cards
        assert Thread._detect_jsx_card_markup("Hello world") is False
        assert Thread._detect_jsx_card_markup("<div>not a card</div>") is False
        assert Thread._detect_jsx_card_markup("") is False


class TestThreadPost:
    async def test_post_string_emits_aimessage(self):
        # plain string is wrapped in AIMessage and emitted via writer
        writer = MagicMock()
        thread = make_thread(writer=writer)
        result = await thread.post("Hello world")

        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, MessageCustomEvent)
        assert event.message.content == "Hello world"
        assert isinstance(result, AIMessage)

    async def test_post_jsx_card_emits_artifact(self):
        # JSX card string is converted to an Artifact and emitted
        writer = MagicMock()
        thread = make_thread(writer=writer)
        result = await thread.post("<Card><Title>Hello</Title></Card>")

        writer.assert_called_once()
        event = writer.call_args[0][0]
        assert isinstance(event, ArtifactCustomEvent)
        assert isinstance(result, Artifact)

    async def test_post_aimessage_emits_message_event(self):
        # pre-built AIMessage is emitted as-is without modification
        writer = MagicMock()
        thread = make_thread(writer=writer)
        msg = AIMessage(content="Pre-built message")
        result = await thread.post(msg)

        event = writer.call_args[0][0]
        assert isinstance(event, MessageCustomEvent)
        assert event.message is msg
        assert result is msg

    async def test_post_chunk_emits_message_event(self):
        # AIMessageChunk takes its own branch (not collapsed into AIMessage despite inheritance)
        writer = MagicMock()
        thread = make_thread(writer=writer)
        chunk = AIMessageChunk(content="chunk")
        result = await thread.post(chunk)

        event = writer.call_args[0][0]
        assert isinstance(event, MessageCustomEvent)
        assert event.message is chunk
        # result must be the original chunk, not an AIMessage
        assert type(result) is AIMessageChunk

    async def test_post_async_iterator_of_strings(self):
        # async iterator of strings emits each chunk then a final AIMessage
        writer = MagicMock()
        thread = make_thread(writer=writer)

        async def gen():
            yield "hello"
            yield " world"

        result = await thread.post(gen())

        # 2 chunk emissions + 1 final AIMessage emission
        assert writer.call_count == 3
        assert isinstance(result, AIMessage)
        assert result.content == "hello world"

    async def test_post_async_iterator_of_chunks(self):
        # async iterator of AIMessageChunk accumulates content into final AIMessage
        writer = MagicMock()
        thread = make_thread(writer=writer)

        async def gen():
            yield AIMessageChunk(content="part1")
            yield AIMessageChunk(content="part2")

        result = await thread.post(gen())
        assert writer.call_count == 3
        assert isinstance(result, AIMessage)
        assert result.content == "part1part2"

    async def test_post_async_iterator_exception_returns_accumulated(self):
        # if the iterator raises mid-stream, content accumulated so far is still returned
        writer = MagicMock()
        thread = make_thread(writer=writer)

        async def gen():
            yield "partial"
            raise RuntimeError("stream broke")

        result = await thread.post(gen())

        assert isinstance(result, AIMessage)
        assert result.content == "partial"

    async def test_post_async_iterator_empty_returns_none(self):
        # iterator that yields nothing returns None (nothing to accumulate)
        writer = MagicMock()
        thread = make_thread(writer=writer)

        async def gen():
            return
            yield  # make it an async generator

        result = await thread.post(gen())
        assert result is None

    async def test_post_async_iterator_unsupported_type_skipped(self):
        # unsupported types yielded from iterator are skipped with a warning
        writer = MagicMock()
        thread = make_thread(writer=writer)

        async def gen():
            yield "valid"
            yield 42  # unsupported — should be skipped
            yield " text"

        result = await thread.post(gen())
        assert result.content == "valid text"

    async def test_post_jsx_card_with_metadata(self):
        # metadata is merged into card artifact metadata when provided
        writer = MagicMock()
        thread = make_thread(writer=writer)
        result = await thread.post("<Card/>", metadata={"custom": "value"})

        event = writer.call_args[0][0]
        assert isinstance(event, ArtifactCustomEvent)
        assert isinstance(result, Artifact)

    async def test_post_unsupported_type_returns_none(self):
        # unsupported content type logs a warning and returns None without writing
        writer = MagicMock()
        thread = make_thread(writer=writer)
        result = await thread.post(12345)

        assert result is None
        writer.assert_not_called()

    async def test_post_returns_none_when_no_writer(self):
        # no stream writer available → post silently returns None
        ctx = make_mock_context()
        thread = Thread(
            context=ctx, id="C1", parent_id=None,
            network="A2A", default_reply_target="C1", writer=None,
        )

        with patch("aion.langgraph.runtime.context.thread.get_stream_writer", side_effect=RuntimeError("no writer")):
            result = await thread.post("Hello")

        assert result is None


class TestThreadReply:
    async def test_reply_with_event_payload_builds_routing(self):
        # reply() attaches routing built from the inbound event context
        payload = Mock()
        payload.context_id = "C123"
        payload.trajectory = "conversation"
        payload.parent_context_id = None
        payload.message_id = "msg1"
        ctx = make_mock_context(event=make_mock_event(payload=payload))

        writer = MagicMock()
        thread = Thread.from_context(ctx, writer=writer)
        await thread.reply("Hello")

        event_emitted = writer.call_args[0][0]
        assert isinstance(event_emitted, MessageCustomEvent)
        assert event_emitted.routing is not None
        assert event_emitted.routing.context_id == "C123"

    async def test_reply_without_event_has_no_routing(self):
        # reply() without an event falls back to default distribution routing (None)
        ctx = make_mock_context(event=None)
        writer = MagicMock()
        thread = Thread.from_context(ctx, writer=writer)
        await thread.reply("Hello")

        event_emitted = writer.call_args[0][0]
        assert event_emitted.routing is None

    def test_build_payload_returns_none_without_event(self):
        # no event → no routing payload
        ctx = make_mock_context(event=None)
        thread = make_thread(context=ctx)
        assert thread._build_message_action_payload() is None

    def test_build_payload_returns_none_without_context_id(self):
        # event payload missing context_id → no routing payload
        payload = Mock()
        payload.context_id = None
        ctx = make_mock_context(event=make_mock_event(payload=payload))
        thread = make_thread(context=ctx)
        assert thread._build_message_action_payload() is None

    def test_build_payload_returns_none_without_payload(self):
        # event with no payload object → no routing payload
        ctx = make_mock_context(event=make_mock_event(payload=None))
        thread = make_thread(context=ctx)
        assert thread._build_message_action_payload() is None

    def test_build_payload_with_all_fields(self):
        # all event payload fields are mapped to MessageActionPayload correctly
        from aion.shared.types.a2a.extensions.messaging import MessageActionPayload

        payload = Mock()
        payload.context_id = "C123"
        payload.trajectory = "reply"
        payload.parent_context_id = "C-parent"
        payload.message_id = "msg-xyz"
        ctx = make_mock_context(event=make_mock_event(payload=payload))
        thread = make_thread(context=ctx)

        result = thread._build_message_action_payload()
        assert isinstance(result, MessageActionPayload)
        assert result.context_id == "C123"
        assert result.trajectory == "reply"
        assert result.parent_context_id == "C-parent"
        assert result.reply_to_message_id == "msg-xyz"


class TestThreadTyping:
    async def test_typing_string_emits_ephemeral(self):
        # string content is emitted as an ephemeral (stream-only) message
        writer = MagicMock()
        thread = make_thread(writer=writer)
        await thread.typing("Processing...")

        event = writer.call_args[0][0]
        assert isinstance(event, MessageCustomEvent)
        assert event.ephemeral is True

    async def test_typing_message_types_emit_ephemeral(self):
        # AIMessage and AIMessageChunk are both forwarded as ephemeral
        for content in [AIMessage(content="Working..."), AIMessageChunk(content="chunk")]:
            writer = MagicMock()
            thread = make_thread(writer=writer)
            await thread.typing(content)
            assert writer.call_args[0][0].ephemeral is True

    async def test_typing_unsupported_type_does_nothing(self):
        # unsupported type logs a warning and produces no emission
        writer = MagicMock()
        thread = make_thread(writer=writer)
        await thread.typing({"not": "supported"})

        writer.assert_not_called()

    async def test_typing_returns_early_when_no_writer(self):
        # typing silently does nothing when no stream writer is available
        ctx = make_mock_context()
        thread = Thread(
            context=ctx, id="C1", parent_id=None,
            network="A2A", default_reply_target="C1", writer=None,
        )

        with patch("aion.langgraph.runtime.context.thread.get_stream_writer", side_effect=RuntimeError("no writer")):
            await thread.typing("Processing...")  # must not raise
