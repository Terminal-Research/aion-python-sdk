from typing import Any, Dict, Literal, Optional

from aion.shared.a2a import A2ABaseModel

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

    user_id: str
    context_id: str
    message_id: str
    trajectory: MessageEventTrajectory
    parent_context_id: Optional[str] = None


class ReactionEventPayload(A2ABaseModel):
    """Reaction or emoji-style activity applied to an existing message."""

    user_id: str
    context_id: str
    message_id: str
    reaction_key: str
    action: str  # "added" / "removed"
    display_value: Optional[str] = None
    is_custom: Optional[bool] = None
    parent_context_id: Optional[str] = None


class CommandEventPayload(A2ABaseModel):
    """Command-style invocation sent through a messaging provider (slash commands, app commands)."""

    user_id: str
    context_id: str
    command: str
    arguments: Optional[str] = None
    invocation_id: Optional[str] = None
    parent_context_id: Optional[str] = None


class SourceSystemEventPayload(A2ABaseModel):
    """Verbatim provider event preserved for downstream inspection beyond the normalized payload."""

    provider: str
    event: Dict[str, Any]


class MessageActionPayload(A2ABaseModel):
    """Routing target for an outbound message sent to a distribution."""

    trajectory: MessageActionTrajectory
    context_id: str
    parent_context_id: Optional[str] = None
    user_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None


class ReactionActionPayload(A2ABaseModel):
    """Instructs a distribution to add or remove a reaction on an existing provider message."""

    context_id: str
    message_id: str
    reaction_key: str
    operation: Literal["add", "remove"]
    display_value: Optional[str] = None
