"""Provider-neutral LangGraph patterns for a Slack distribution.

Slack app provisioning and direct Web API calls belong to the Aion control
plane. Agent code consumes normalized Distribution/Messaging context and uses
the incoming distribution's primary MCP capability for provider-native reads.
``Thread.history()`` is not used because that SDK helper is not implemented.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypedDict

from a2a.types import Message as A2AMessage
from a2a.types import Part, Role
from aion.api import (
    CapabilitySubjectSource,
    RuntimeCapabilityReference,
)
from aion.core.a2a import A2AInbox, A2AOutbox
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.runtime.context import AionRuntimeContext
from aion.langgraph.authoring import create_event_router, load_aion_mcp_tools
from aion.langgraph.authoring.invocation.message import Message
from aion.langgraph.authoring.invocation.thread import Thread
from langchain_core.messages import AIMessage


class PlainState(TypedDict):
    """Minimal state for an ordinary LangGraph message workflow."""

    messages: list[Any]


class HybridState(PlainState):
    """LangGraph state that also exposes an explicit A2A inbox."""

    a2a_inbox: A2AInbox


class HybridUpdate(TypedDict):
    """State update carrying an explicit A2A response."""

    a2a_outbox: A2AOutbox


def plain_langgraph_reply(state: PlainState) -> PlainState:
    """Return a normal LangGraph response for Slack-originated input.

    Args:
        state: Graph state containing the conversation's LangChain messages.

    Returns:
        A state update containing an ordinary assistant message.
    """
    latest = state["messages"][-1]
    content = getattr(latest, "content", str(latest))
    return {"messages": [AIMessage(content=f"Received: {content}")]}


def hybrid_a2a_reply(state: HybridState) -> HybridUpdate:
    """Build an explicit A2A response while preserving its opaque context.

    The top-level A2A context is intentionally independent from Slack's
    channel and thread coordinates. Each inbound Slack turn starts a new task
    even when multiple turns share this long-lived context.

    Args:
        state: Graph state containing an A2A inbox and LangChain messages.

    Returns:
        A state update with an explicit A2A outbox response.

    Raises:
        ValueError: If the graph was invoked without an inbound A2A message.
    """
    incoming = state["a2a_inbox"].message
    if incoming is None:
        raise ValueError("hybrid A2A handling requires an inbound message")

    response = A2AMessage(
        context_id=incoming.context_id,
        role=Role.ROLE_AGENT,
        parts=[Part(text="Handled through the explicit A2A outbox.")],
    )
    return {"a2a_outbox": A2AOutbox(message=response)}


def slack_history_request(
    thread: Thread,
    *,
    limit: int = 15,
) -> tuple[str, dict[str, Any]]:
    """Map normalized context coordinates to a Slack MCP history request.

    Args:
        thread: Current invocation thread resolved from Messaging payload data.
        limit: Maximum number of provider messages requested.

    Returns:
        The provider-native MCP tool name and arguments.

    Raises:
        ValueError: If the inbound event has no usable context identifier.
    """
    if thread.context_id is None:
        raise ValueError("Slack history requires a conversation context")
    if thread.parent_context_id is not None:
        return (
            "conversations.replies",
            {
                "channel": thread.parent_context_id,
                "ts": thread.context_id,
                "limit": limit,
            },
        )
    return (
        "conversations.history",
        {"channel": thread.context_id, "limit": limit},
    )


async def read_recent_slack_history(
    context: AionRuntimeContext,
    thread: Thread,
    *,
    limit: int = 15,
) -> Any:
    """Read Slack history through the incoming distribution's primary MCP.

    The runtime capability reference lets Aion resolve the active distribution,
    installation, authorization, and token. Agent code supplies neither a
    static distribution identifier nor a Slack credential.

    Args:
        context: Runtime context for the inbound distribution invocation.
        thread: Current normalized conversation context.
        limit: Maximum number of provider messages requested.

    Returns:
        The selected Slack MCP tool's result, including provider freshness
        metadata when the service returns cached history.

    Raises:
        LookupError: If the distribution does not expose the required tool.
    """
    tool_name, arguments = slack_history_request(thread, limit=limit)
    tools = await load_aion_mcp_tools(
        context,
        runtime_capability_references=[
            RuntimeCapabilityReference.primary_mcp(
                CapabilitySubjectSource.INCOMING_DISTRIBUTION
            )
        ],
    )
    tool = next((candidate for candidate in tools if candidate.name == tool_name), None)
    if tool is None:
        raise LookupError(f"Slack MCP tool is unavailable: {tool_name}")
    return await tool.ainvoke(arguments)


async def handle_slack_message(thread: Thread, message: Message) -> None:
    """Reply to a normalized Slack message through distribution routing.

    Args:
        thread: Current Slack channel, DM, MPIM, or thread context.
        message: Normalized inbound message.
    """
    await thread.reply(f"Received: {message.text or ''}")


async def handle_slack_reaction(message: Message) -> None:
    """Acknowledge a normalized Slack reaction on its target message.

    Args:
        message: Message wrapper bound to the reaction target coordinates.
    """
    await message.react("eyes")


slack_events = create_event_router(
    on_message=handle_slack_message,
    on_reaction=handle_slack_reaction,
)
"""Router node for normalized Slack message and reaction events."""


async def post_to_enclosing_conversation(
    thread: Thread,
    content: str,
) -> None:
    """Post outside a Slack thread without embedding a distribution id.

    Args:
        thread: Current normalized Slack context.
        content: Message text to post to the enclosing channel or chat.

    Raises:
        ValueError: If no channel, DM, or MPIM context is available.
    """
    context_id = thread.parent_context_id or thread.context_id
    if context_id is None:
        raise ValueError("Slack posting requires a conversation context")
    target = MessageActionPayload(
        trajectory="conversation",
        context_id=context_id,
    )
    await thread.post(content, target=target)


async def stream_slack_reply(
    thread: Thread,
    chunks: Iterable[str],
) -> None:
    """Stream text chunks as a reply in the current Slack context.

    Args:
        thread: Current normalized Slack context.
        chunks: Ordered text fragments produced by an agent or model.
    """

    async def stream():
        for chunk in chunks:
            yield chunk

    await thread.reply(stream())
