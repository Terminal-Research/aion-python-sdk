"""Contract tests for the Slack distribution authoring examples."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from a2a.types import Message as A2AMessage
from a2a.types import Part, Role
from aion.api import CapabilitySubjectSource
from aion.core.a2a import A2AInbox
from aion.core.a2a.extensions.messaging import (
    MessageEventPayload,
    ReactionEventPayload,
)
from aion.core.runtime.context import AionRuntimeContext, Event, EventKind
from aion.langgraph.authoring.events.custom_events import (
    MessageCustomEvent,
    ReactionCustomEvent,
)
from aion.langgraph.authoring.invocation.thread import Thread
from examples.langgraph import slack_distribution
from langchain_core.messages import AIMessage, HumanMessage

from .helpers import make_mock_distribution_extension, make_mock_runtime

_GET_STREAM_WRITER = (
    "aion.langgraph.authoring.invocation.thread.get_stream_writer"
)


def _slack_context(
    payload: MessageEventPayload | ReactionEventPayload,
    *,
    kind: EventKind = EventKind.MESSAGE,
) -> AionRuntimeContext:
    """Build a runtime context with distinct A2A and Slack identifiers.

    Args:
        payload: Normalized Slack event payload.
        kind: Event kind represented by the payload.

    Returns:
        A runtime context suitable for the authoring examples.
    """
    inbound = A2AMessage(
        message_id="a2a-message-1",
        context_id="ctx-a2a-long-lived",
        role=Role.ROLE_USER,
        parts=[Part(text="Please summarize the thread.")],
    )
    event = Event(
        kind=kind,
        id="Ev01SLACK",
        source="https://slack.com/events/A0APP123",
        payload=payload,
        raw=None,
    )
    return AionRuntimeContext(
        inbox=A2AInbox(message=inbound),
        event=event,
        distribution_extension_payload=make_mock_distribution_extension(
            endpoint_type="Slack",
            include_service=True,
        ),
    )


def _thread_context() -> AionRuntimeContext:
    """Build a Slack thread-message context."""
    return _slack_context(
        MessageEventPayload(
            user_id="U0HUMAN",
            context_id="1715182600.001900",
            parent_context_id="C0CHANNEL1",
            message_id="1715182634.002500",
            reply_to_message_id="1715182600.001900",
            trajectory="reply",
        )
    )


def test_plain_and_hybrid_examples_keep_context_layers_separate() -> None:
    """Plain state works while explicit A2A replies keep the A2A context."""
    plain = slack_distribution.plain_langgraph_reply(
        {"messages": [HumanMessage(content="Hello from Slack")]}
    )
    context = _thread_context()
    hybrid = slack_distribution.hybrid_a2a_reply(
        {
            "messages": [HumanMessage(content="Hello from Slack")],
            "a2a_inbox": context.inbox,
        }
    )

    assert plain["messages"][0].content == "Received: Hello from Slack"
    assert hybrid["a2a_outbox"].message.context_id == "ctx-a2a-long-lived"
    assert (
        hybrid["a2a_outbox"].message.context_id
        != context.event.payload.context_id
    )


def test_thread_history_request_uses_normalized_slack_coordinates() -> None:
    """Thread history maps the thread timestamp and parent channel exactly."""
    thread = Thread.from_context(_thread_context())

    tool_name, arguments = slack_distribution.slack_history_request(
        thread,
        limit=12,
    )

    assert thread.context_id == "1715182600.001900"
    assert thread.parent_context_id == "C0CHANNEL1"
    assert tool_name == "conversations.replies"
    assert arguments == {
        "channel": "C0CHANNEL1",
        "ts": "1715182600.001900",
        "limit": 12,
    }


async def test_history_loads_the_incoming_distribution_primary_mcp(
    monkeypatch,
) -> None:
    """History resolution uses runtime capability addressing and no token."""
    captured = {}

    class HistoryTool:
        """Minimal LangChain-compatible history tool double."""

        name = "conversations.replies"

        async def ainvoke(self, arguments):
            """Return captured invocation arguments."""
            captured["arguments"] = arguments
            return {"messages": [], "freshness": {"source": "live"}}

    async def load_tools(context, *, runtime_capability_references):
        captured["context"] = context
        captured["references"] = runtime_capability_references
        return [HistoryTool()]

    monkeypatch.setattr(
        slack_distribution,
        "load_aion_mcp_tools",
        load_tools,
    )
    context = _thread_context()

    result = await slack_distribution.read_recent_slack_history(
        context,
        Thread.from_context(context),
    )

    reference = captured["references"][0]
    assert captured["context"] is context
    assert reference.source is CapabilitySubjectSource.INCOMING_DISTRIBUTION
    assert captured["arguments"] == {
        "channel": "C0CHANNEL1",
        "ts": "1715182600.001900",
        "limit": 15,
    }
    assert result["freshness"]["source"] == "live"


async def test_event_router_replies_to_normalized_slack_message() -> None:
    """The message route emits a reply to the immediate Slack thread."""
    writer = MagicMock()
    context = _thread_context()

    with patch(_GET_STREAM_WRITER, return_value=writer):
        await slack_distribution.slack_events(
            {},
            runtime=make_mock_runtime(context),
        )

    event = writer.call_args[0][0]
    assert isinstance(event, MessageCustomEvent)
    assert event.message.content == "Received: Please summarize the thread."
    assert event.routing.context_id == "1715182600.001900"
    assert event.routing.parent_context_id == "C0CHANNEL1"
    assert event.routing.reply_to_message_id == "1715182634.002500"


async def test_event_router_reacts_to_the_slack_target_message() -> None:
    """The reaction route preserves target context and message coordinates."""
    context = _slack_context(
        ReactionEventPayload(
            user_id="U0HUMAN",
            context_id="1715182600.001900",
            parent_context_id="C0CHANNEL1",
            message_id="1715182634.002500",
            reaction_key="thumbsup",
            action="added",
        ),
        kind=EventKind.REACTION,
    )
    writer = MagicMock()

    with patch(_GET_STREAM_WRITER, return_value=writer):
        await slack_distribution.slack_events(
            {},
            runtime=make_mock_runtime(context),
        )

    event = writer.call_args[0][0]
    assert isinstance(event, ReactionCustomEvent)
    assert event.payload.context_id == "1715182600.001900"
    assert event.payload.message_id == "1715182634.002500"
    assert event.payload.reaction_key == "eyes"


async def test_post_and_stream_helpers_preserve_distribution_routing() -> None:
    """Explicit posts and streamed replies stay on normalized Slack targets."""
    thread = Thread.from_context(_thread_context())
    writer = MagicMock()

    with patch(_GET_STREAM_WRITER, return_value=writer):
        await slack_distribution.post_to_enclosing_conversation(
            thread,
            "Channel update",
        )
        await slack_distribution.stream_slack_reply(
            thread,
            ["First ", "second"],
        )

    events = [call.args[0] for call in writer.call_args_list]
    channel_post = events[0]
    final_stream_message = events[-1]
    assert isinstance(channel_post, MessageCustomEvent)
    assert channel_post.routing.context_id == "C0CHANNEL1"
    assert channel_post.routing.parent_context_id is None
    assert isinstance(final_stream_message.message, AIMessage)
    assert final_stream_message.message.content == "First second"
    assert final_stream_message.routing.context_id == "1715182600.001900"
