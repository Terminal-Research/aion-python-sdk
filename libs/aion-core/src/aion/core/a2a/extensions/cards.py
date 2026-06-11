"""A2A extension models for card interactions.

Defines payload models for card action events (user interactions with rendered cards).
"""

from typing import Optional

from pydantic import Field

from aion.core.a2a import A2ABaseModel

__all__ = [
    "CardActionEventPayload",
]


class CardActionEventPayload(A2ABaseModel):
    """User interaction with a previously rendered card (click, submit, or activate).

    See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
    """

    user_id: str = Field(description="Actor who triggered the card action.")
    context_id: str = Field(
        description="Channel, space, or conversation id where the action occurred.",
    )
    action_id: str = Field(
        description="Developer-defined action identifier echoed back by the provider.",
    )
    parent_context_id: Optional[str] = Field(
        default=None,
        description="Thread or parent conversation id when nested context exists.",
    )
