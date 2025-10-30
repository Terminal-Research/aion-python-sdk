"""Universal input model for agent execution.

This module defines AgentInput, a framework-agnostic input representation that
captures what the USER sends. Each ExecutorAdapter transforms it to framework format.
"""

from pydantic import BaseModel, Field


class AgentInput(BaseModel):
    """Universal input model representing user input.

    This is what the USER sends (framework-agnostic).
    Framework adapters transform this to their specific format.

    Attributes:
        text: User's text message (currently the only field used)
    """

    text: str = Field(description="User's text message")
