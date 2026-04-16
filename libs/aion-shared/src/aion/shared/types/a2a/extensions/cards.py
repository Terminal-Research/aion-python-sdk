from typing import Optional

from aion.shared.a2a import A2ABaseModel

__all__ = [
    "CARDS_EXTENSION_URI_V1",
    "CardActionEventPayload",
]

CARDS_EXTENSION_URI_V1 = (
    "https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0"
)


class CardActionEventPayload(A2ABaseModel):
    """User interaction with a previously rendered card (click, submit, or activate)."""

    user_id: str
    context_id: str
    action_id: str
    parent_context_id: Optional[str] = None
