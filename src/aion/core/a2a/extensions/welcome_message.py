"""Typed welcome intent; agent implementations own greeting generation."""

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from aion.core.a2a import A2ABaseModel

if TYPE_CHECKING:
    from a2a.types import Message

__all__ = ["WelcomeRequestPayload", "has_user_text"]


class WelcomeRequestPayload(A2ABaseModel):
    """Schema-tagged data part requesting an opening conversation message.

    This payload supplies no prompt or personalization. An enabled agent
    handles it using normal execution, output, and task-completion APIs.
    """

    type: Literal["welcome-request"] = Field(description="Welcome request intent.")


def has_user_text(message: "Message | None") -> bool:
    """Return whether actual user text takes precedence over welcome intent.

    Args:
        message: Inbound protocol message, when present.

    Returns:
        True when at least one text part contains non-whitespace content.
    """
    return message is not None and any(part.text.strip() for part in message.parts)
