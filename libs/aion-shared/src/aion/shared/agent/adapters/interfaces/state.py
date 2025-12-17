"""Abstract base class for agent state extraction and management.

This module provides classes and interfaces for extracting, managing, and
manipulating agent state across different frameworks.

Key classes:
- AgentState: Unified representation of agent execution state
- InterruptInfo: Information about execution interrupts/pauses
- StateExtractor: Base class for extracting data from framework-specific objects
- StateAdapter: (deprecated) Will be removed in favor of plugin-specific implementations
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


class StateExtractor(ABC):
    """Abstract base for extracting specific data from framework-specific objects.

    StateExtractor provides a composable pattern for extracting different pieces
    of information from framework-specific state/session/snapshot objects.

    Plugins can create multiple specialized extractors (e.g., ValuesExtractor,
    MessagesExtractor, MetadataExtractor) to build up an AgentState object.

    Example:
        class ValuesExtractor(StateExtractor):
            def extract(self, session):
                return session.state

            def can_extract(self, session):
                return hasattr(session, 'state')
    """

    @abstractmethod
    def extract(self, source: Any) -> Any:
        """Extract information from a framework-specific object.

        Args:
            source: Framework-specific object (e.g., Session, StateSnapshot)

        Returns:
            Any: Extracted information (type depends on extractor implementation)
        """
        pass

    @abstractmethod
    def can_extract(self, source: Any) -> bool:
        """Check if this extractor can extract from the given source.

        Args:
            source: Framework-specific object to check

        Returns:
            bool: True if extraction is possible, False otherwise
        """
        pass


class StateAdapter(ABC):
    # todo remove state adapter (current implementation looks like it was developed for langgraph and not suitable for adk etc)
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

