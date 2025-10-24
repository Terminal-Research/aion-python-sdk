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
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentState:
    """Unified representation of agent execution state.

    Attributes:
        values: Dictionary of current state values
        next_steps: List of upcoming steps/nodes in execution
        is_interrupted: Whether execution is paused/interrupted
        metadata: Additional state metadata
        config: Execution configuration
        messages: List of messages in conversation history
    """
    values: dict[str, Any] = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)
    is_interrupted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    messages: list[Any] = field(default_factory=list)

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


@dataclass
class InterruptInfo:
    """Information about an agent execution interrupt/pause.

    Attributes:
        reason: Description of why execution was interrupted
        prompt: Optional prompt/question for the user
        options: Optional list of valid user responses
        metadata: Additional interrupt metadata
    """
    reason: str
    prompt: Optional[str] = None
    options: Optional[list[str]] = None
    metadata: dict[str, Any] = field(default_factory=dict)

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

