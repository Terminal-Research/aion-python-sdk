"""A2A extension models for message events and actions.

Defines payload models for message-based events (messages, reactions, commands),
and action payloads for routing message responses through distribution channels.
"""

from typing import Any, Dict, Literal, Optional

from pydantic import Field

from aion.core.a2a import A2ABaseModel

__all__ = [
    "MessageEventTrajectory",
    "MessageActionTrajectory",
    "MessageEventPayload",
    "ReactionEventPayload",
    "CommandEventPayload",
    "SourceSystemEventPayload",
    "MessageActionPayload",
    "ReactionActionPayload",
]

MessageEventTrajectory = Literal["direct-message", "reply", "conversation", "timeline"]
MessageActionTrajectory = MessageEventTrajectory


class MessageEventPayload(A2ABaseModel):
    """Normalized inbound message context (DM, mention, thread reply, or channel message)."""

    user_id: str = Field(description="Sender identifier on the source network.")
    context_id: str = Field(description="Source-network conversation, room, or thread id.")
    message_id: str = Field(description="Source-network message id.")
    trajectory: MessageEventTrajectory = Field(
        description=(
            "Routing context for the message: direct-message, reply, conversation, or timeline."
        ),
    )
    parent_context_id: Optional[str] = Field(
        default=None,
        description="Parent context id when nested threads are used.",
    )


class ReactionEventPayload(A2ABaseModel):
    """Reaction or emoji-style activity applied to an existing message."""

    user_id: str = Field(description="Actor who added or removed the reaction.")
    context_id: str = Field(description="Source-network conversation, room, or thread id.")
    message_id: str = Field(description="Target message id on the source network.")
    reaction_key: str = Field(description="Provider-stable reaction identifier.")
    action: str = Field(description="Reaction transition: 'added' or 'removed'.")
    display_value: Optional[str] = Field(
        default=None,
        description="Human-readable emoji or provider label when available.",
    )
    is_custom: Optional[bool] = Field(
        default=None,
        description="Whether the reaction is provider-specific rather than a standard emoji.",
    )
    parent_context_id: Optional[str] = Field(
        default=None,
        description="Parent context id when nested threads are used.",
    )


class CommandEventPayload(A2ABaseModel):
    """Command-style invocation sent through a messaging provider (slash commands, app commands)."""

    user_id: str = Field(description="Actor who invoked the command.")
    context_id: str = Field(description="Room, channel, thread, or DM context where the command ran.")
    command: str = Field(description="Provider-visible command token, e.g. '/deploy'.")
    arguments: Optional[str] = Field(
        default=None,
        description="Raw argument string supplied after the command token.",
    )
    invocation_id: Optional[str] = Field(
        default=None,
        description="Provider-native command invocation id when available.",
    )
    parent_context_id: Optional[str] = Field(
        default=None,
        description="Parent context id when nested threads are used.",
    )


class SourceSystemEventPayload(A2ABaseModel):
    """Verbatim provider event preserved for downstream inspection beyond the normalized payload."""

    provider: str = Field(description="Source provider name, e.g. 'slack' or 'telegram'.")
    event: Dict[str, Any] = Field(description="Verbatim event payload from the source system.")


class MessageActionPayload(A2ABaseModel):
    """Routing target for an outbound message sent to a distribution."""

    trajectory: MessageActionTrajectory = Field(
        description=(
            "Target delivery mode: direct-message, reply, conversation, or timeline."
        ),
    )
    context_id: str = Field(description="Target conversation, thread, or channel context.")
    parent_context_id: Optional[str] = Field(
        default=None,
        description=(
            "Parent context when the target is nested inside a broader context. "
            "Used for provider constructs such as Telegram Forum Topics, where "
            "contextId is the group and parentContextId is the topic id."
        ),
    )
    user_id: Optional[str] = Field(
        default=None,
        description="Target user id; required when trajectory is 'direct-message'.",
    )
    reply_to_message_id: Optional[str] = Field(
        default=None,
        description="Provider message id being replied to; required when trajectory is 'reply'.",
    )


class ReactionActionPayload(A2ABaseModel):
    """Instructs a distribution to add or remove a reaction on an existing provider message."""

    context_id: str = Field(description="Target conversation, thread, or channel context.")
    message_id: str = Field(description="Provider message id to react to.")
    reaction_key: str = Field(
        description="Provider-stable reaction identifier, e.g. 'thumbsup' or 'heart'.",
    )
    operation: Literal["add", "remove"] = Field(description="Reaction action: 'add' or 'remove'.")
    display_value: Optional[str] = Field(
        default=None,
        description="Human-readable emoji or label, e.g. ':thumbsup:'.",
    )
