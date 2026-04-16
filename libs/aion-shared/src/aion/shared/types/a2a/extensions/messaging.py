from typing import Any, Dict, Literal, Optional

from aion.shared.a2a import A2ABaseModel

__all__ = [
    "MESSAGING_EXTENSION_URI_V1",
    "MessageEventPayload",
    "ReactionEventPayload",
    "CommandEventPayload",
    "SourceSystemEventPayload",
]

MESSAGING_EXTENSION_URI_V1 = (
    "https://docs.aion.to/a2a/extensions/aion/distribution/messaging/1.0.0"
)


class MessageEventPayload(A2ABaseModel):
    """Normalized inbound message context (DM, mention, thread reply, or channel message)."""

    user_id: str
    context_id: str
    message_id: str
    trajectory: Literal["direct-message", "reply", "timeline", "conversation"]
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
