from typing import Optional

from aion.shared.a2a import A2ABaseModel

__all__ = [
    "CardActionEventPayload",
]


class CardActionEventPayload(A2ABaseModel):
    """User interaction with a previously rendered card (click, submit, or activate).

    See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
    """

    user_id: str
    context_id: str
    action_id: str
    parent_context_id: Optional[str] = None
