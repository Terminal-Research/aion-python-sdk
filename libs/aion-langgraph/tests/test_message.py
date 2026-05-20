import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from aion.langgraph.runtime.context.message import Message, User
from aion.langgraph.events.custom_events import ReactionCustomEvent
from aion.core.constants import EVENT_EXTENSION_URI_V1

from tests.helpers import make_mock_context, make_mock_event, make_mock_inbox


def make_mock_part(text=None, metadata=None):
    part = Mock()
    part.text = text
    part.metadata = metadata if metadata is not None else {}
    return part


def make_message(context=None, thread=None):
    ctx = context or make_mock_context(event=None)
    t = thread or Mock()
    return Message(context=ctx, thread=t)


class TestMessageParseId:
    def test_returns_id_from_event_payload(self):
        # message_id is extracted from the inbound event payload first
        payload = Mock()
        payload.message_id = "msg-from-event"
        payload.user_id = None
        ctx = make_mock_context(event=make_mock_event(payload=payload))
        msg = make_message(context=ctx)
        assert msg.id == "msg-from-event"

    def test_falls_back_to_inbox_message_id(self):
        # when no event payload, message_id comes from inbox.message
        inbox_msg = Mock()
        inbox_msg.message_id = "msg-from-inbox"
        inbox_msg.parts = []
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))
        msg = make_message(context=ctx)
        assert msg.id == "msg-from-inbox"

    def test_returns_none_when_no_source(self):
        # id is None when neither event payload nor inbox message is present
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=None))
        msg = make_message(context=ctx)
        assert msg.id is None

    def test_payload_message_id_takes_precedence_over_inbox(self):
        # event payload message_id wins over inbox.message.message_id
        payload = Mock()
        payload.message_id = "from-event"
        payload.user_id = None
        inbox_msg = Mock()
        inbox_msg.message_id = "from-inbox"
        inbox_msg.parts = []
        ctx = make_mock_context(
            event=make_mock_event(payload=payload),
            inbox=make_mock_inbox(message=inbox_msg),
        )
        msg = make_message(context=ctx)
        assert msg.id == "from-event"


class TestMessageParseText:
    def test_joins_text_parts(self):
        # multiple text parts are joined with newline
        parts = [make_mock_part(text="Hello"), make_mock_part(text="World")]
        inbox_msg = Mock()
        inbox_msg.message_id = "m1"
        inbox_msg.parts = parts
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))
        msg = make_message(context=ctx)
        assert msg.text == "Hello\nWorld"

    def test_single_part(self):
        # single text part is returned without extra newlines
        inbox_msg = Mock()
        inbox_msg.message_id = "m1"
        inbox_msg.parts = [make_mock_part(text="Only one")]
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))
        msg = make_message(context=ctx)
        assert msg.text == "Only one"

    def test_filters_extension_uri_parts(self):
        # parts carrying EVENT_EXTENSION_URI_V1 metadata are excluded from text
        normal = make_mock_part(text="real text")
        ext = make_mock_part(text="extension data", metadata={EVENT_EXTENSION_URI_V1: "some-value"})
        inbox_msg = Mock()
        inbox_msg.message_id = "m1"
        inbox_msg.parts = [normal, ext]
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))
        msg = make_message(context=ctx)
        assert msg.text == "real text"

    def test_returns_none_when_no_inbox_message(self):
        # text is None when the inbox carries no message
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=None))
        msg = make_message(context=ctx)
        assert msg.text is None

    def test_returns_none_when_all_parts_filtered(self):
        # text is None when all parts are stripped by the extension filter
        ext = make_mock_part(text="data", metadata={EVENT_EXTENSION_URI_V1: "val"})
        inbox_msg = Mock()
        inbox_msg.message_id = "m1"
        inbox_msg.parts = [ext]
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))
        msg = make_message(context=ctx)
        assert msg.text is None

    def test_returns_none_when_parts_list_is_empty(self):
        # text is None when message has no parts at all
        inbox_msg = Mock()
        inbox_msg.message_id = "m1"
        inbox_msg.parts = []
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))
        msg = make_message(context=ctx)
        assert msg.text is None

    def test_skips_parts_with_no_text(self):
        # parts with text=None are skipped; remaining parts are joined
        p1 = make_mock_part(text=None)
        p2 = make_mock_part(text="actual text")
        inbox_msg = Mock()
        inbox_msg.message_id = "m1"
        inbox_msg.parts = [p1, p2]
        ctx = make_mock_context(event=None, inbox=make_mock_inbox(message=inbox_msg))
        msg = make_message(context=ctx)
        assert msg.text == "actual text"


class TestMessageParseUser:
    def test_returns_user_from_event_payload(self):
        # user_id from event payload is wrapped in a User dataclass
        payload = Mock()
        payload.message_id = None
        payload.user_id = "user-123"
        ctx = make_mock_context(event=make_mock_event(payload=payload))
        msg = make_message(context=ctx)
        assert isinstance(msg.user, User)
        assert msg.user.id == "user-123"

    def test_returns_none_when_no_event(self):
        # user is None when there is no event (direct A2A without CloudEvents)
        ctx = make_mock_context(event=None)
        msg = make_message(context=ctx)
        assert msg.user is None

    def test_returns_none_when_payload_is_none(self):
        # user is None when the event has no payload object
        ctx = make_mock_context(event=make_mock_event(payload=None))
        msg = make_message(context=ctx)
        assert msg.user is None


class TestMessageReply:
    async def test_delegates_to_thread_reply(self):
        # reply() is a thin wrapper that calls thread.reply() with same args
        mock_thread = Mock()
        mock_thread.reply = AsyncMock(return_value=None)

        ctx = make_mock_context(event=None)
        msg = make_message(context=ctx, thread=mock_thread)
        await msg.reply("Hello", metadata={"key": "val"})

        mock_thread.reply.assert_called_once_with("Hello", metadata={"key": "val"})



class TestMessageReact:
    def _make_react_ready_message(self, context_id="C123", message_id="msg-1"):
        payload = Mock()
        payload.context_id = context_id
        payload.message_id = message_id
        payload.user_id = None
        ctx = make_mock_context(event=make_mock_event(payload=payload))

        writer = MagicMock()
        mock_thread = Mock()
        mock_thread.get_writer = Mock(return_value=writer)

        msg = make_message(context=ctx, thread=mock_thread)
        return msg, writer

    async def test_react_emits_correct_payload(self):
        # react() builds and emits a ReactionActionPayload with all fields set
        msg, _ = self._make_react_ready_message()

        with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
            await msg.react("thumbsup")
            mock_emit.assert_called_once()
            _, reaction_payload = mock_emit.call_args[0]
            assert reaction_payload.context_id == "C123"
            assert reaction_payload.message_id == "msg-1"
            assert reaction_payload.reaction_key == "thumbsup"

    async def test_react_default_operation_is_add(self):
        # operation defaults to "add" when not specified
        msg, _ = self._make_react_ready_message()

        with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
            await msg.react("thumbsup")
            _, reaction_payload = mock_emit.call_args[0]
            assert reaction_payload.operation == "add"

    async def test_react_remove_operation(self):
        # operation="remove" is passed through to the payload
        msg, _ = self._make_react_ready_message()

        with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
            await msg.react("thumbsup", operation="remove")
            _, reaction_payload = mock_emit.call_args[0]
            assert reaction_payload.operation == "remove"

    async def test_react_display_value_propagates(self):
        # display_value is forwarded to the ReactionActionPayload
        msg, _ = self._make_react_ready_message()

        with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
            await msg.react("thumbsup", display_value="+1")
            _, reaction_payload = mock_emit.call_args[0]
            assert reaction_payload.display_value == "+1"

    async def test_react_warns_when_no_event(self):
        # missing event → warning logged, no reaction emitted
        ctx = make_mock_context(event=None)
        msg = make_message(context=ctx)

        with patch("aion.langgraph.runtime.context.message.logger") as mock_logger:
            with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
                await msg.react("thumbsup")
                mock_emit.assert_not_called()
                mock_logger.warning.assert_called_once()

    async def test_react_warns_when_missing_context_id(self):
        # context_id absent in payload → warning logged, no reaction emitted
        payload = Mock()
        payload.message_id = "msg-1"
        payload.context_id = None
        payload.user_id = None
        ctx = make_mock_context(event=make_mock_event(payload=payload))
        msg = make_message(context=ctx)

        with patch("aion.langgraph.runtime.context.message.logger") as mock_logger:
            with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
                await msg.react("thumbsup")
                mock_emit.assert_not_called()
                mock_logger.warning.assert_called_once()

    async def test_react_warns_when_missing_message_id(self):
        # message_id absent in payload → warning logged, no reaction emitted
        payload = Mock()
        payload.message_id = None
        payload.context_id = "C123"
        payload.user_id = None
        ctx = make_mock_context(event=make_mock_event(payload=payload))
        msg = make_message(context=ctx)

        with patch("aion.langgraph.runtime.context.message.logger") as mock_logger:
            with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
                await msg.react("thumbsup")
                mock_emit.assert_not_called()
                mock_logger.warning.assert_called_once()

    async def test_react_does_nothing_when_no_writer(self):
        # no stream writer available → react silently returns without emitting
        payload = Mock()
        payload.context_id = "C123"
        payload.message_id = "msg-1"
        payload.user_id = None
        ctx = make_mock_context(event=make_mock_event(payload=payload))

        mock_thread = Mock()
        mock_thread.get_writer = Mock(return_value=None)
        msg = make_message(context=ctx, thread=mock_thread)

        with patch("aion.langgraph.stream.emit_reaction") as mock_emit:
            await msg.react("thumbsup")
            mock_emit.assert_not_called()
