"""Abstract base class for agent state extraction and management.

This module provides classes and interfaces for extracting, managing, and
manipulating agent state across different frameworks.

The StateAdapter enables:
- State snapshot conversion to unified format
- Interrupt/pause detection and handling
- Resume input generation from user feedback
- Message and metadata extraction
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, Field


class InterruptInfo(BaseModel):
    """Information about an agent execution interrupt/pause."""

    reason: str = Field(description="Description of why execution was interrupted")
    prompt: Optional[str] = Field(
        default=None,
        description="Prompt/question for the user"
    )
    options: Optional[list[str]] = Field(
        default=None,
        description="List of valid user responses"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional interrupt metadata"
    )


class AgentState(BaseModel):
    """Unified representation of agent execution state."""

    values: dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary of current state values"
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="List of upcoming steps/nodes in execution"
    )
    is_interrupted: bool = Field(
        default=False,
        description="Whether execution is paused/interrupted"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional state metadata"
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Execution configuration"
    )
    messages: list[Any] = Field(
        default_factory=list,
        description="List of messages in conversation history"
    )

    def is_complete(self) -> bool:
        """Check if agent execution is complete.

        Returns:
            bool: True if execution has no more steps and is not interrupted
        """
        return len(self.next_steps) == 0 and not self.is_interrupted

    def requires_input(self) -> bool:
        """Check if execution requires user input.

        Returns:
            bool: True if execution is interrupted and waiting for input
        """
        return self.is_interrupted

class StateAdapter(ABC):
    """Abstract base for framework-specific agent state extraction.

    Subclasses must implement methods to convert framework-specific state
    representations to the unified AgentState format.

    The StateAdapter is responsible for:
    - Converting framework state snapshots to unified format
    - Extracting interrupt/pause information
    - Generating resume inputs from user feedback
    - Extracting messages and metadata from state
    """

    @abstractmethod
    def get_state_from_snapshot(self, snapshot: Any) -> AgentState:
        """Extract unified state from a framework-specific state snapshot.

        Args:
            snapshot: A framework-specific state snapshot

        Returns:
            AgentState: The state in unified format

        Raises:
            ValueError: If snapshot cannot be converted
        """
        pass

    @abstractmethod
    def extract_interrupt_info(self, state: AgentState) -> Optional[InterruptInfo]:
        """Extract interrupt information from agent state.

        Args:
            state: The agent state to examine

        Returns:
            Optional[InterruptInfo]: Interrupt information if state is interrupted,
                                    None otherwise
        """
        pass

    @abstractmethod
    def create_resume_input(self, user_input: Any, state: AgentState) -> dict[str, Any]:
        """Create framework-specific resume input from user feedback.

        Args:
            user_input: User's response/feedback after interruption
            state: The current agent state

        Returns:
            dict[str, Any]: Resume input formatted for the framework

        Raises:
            ValueError: If resume input cannot be created
        """
        pass

