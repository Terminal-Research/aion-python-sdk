"""Abstract base class for agent execution state extraction and management.

This module provides classes and interfaces for extracting, managing, and
manipulating agent execution state snapshots across different frameworks.

Key classes:
- ExecutionSnapshot: Unified snapshot of agent execution state at a point in time
- ExecutionStatus: Status of agent execution (running, interrupted, complete, error)
- InterruptInfo: Information about execution interrupts/pauses
- StateExtractor: Base class for extracting data from framework-specific objects
- StateAdapter: (deprecated) Will be removed in favor of plugin-specific implementations
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .messages import Message, MessagePart, MessagePartType, MessageRole


class ExecutionStatus(str, Enum):
    """Status of agent execution in a session."""

    RUNNING = "running"      # Execution is actively running
    INTERRUPTED = "interrupted"  # Execution paused, waiting for input
    COMPLETE = "complete"    # Execution finished successfully
    ERROR = "error"          # Execution encountered an error


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


class ExecutionSnapshot(BaseModel):
    """Unified snapshot of agent execution state at a point in time.

    This class represents a complete snapshot of an agent's execution state,
    including conversation history, state variables, execution status, and metadata.
    Framework-specific data (like next_steps in LangGraph) should be stored in metadata.
    """

    messages: list[Message] = Field(
        default_factory=list,
        description="Conversation history in unified format. "
                    "Framework adapters convert between framework-specific message types "
                    "(e.g., LangChain BaseMessage, ADK Message) and this unified format."
    )
    state: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent state variables dictionary (excluding messages). "
                    "Contains framework-specific state variables, context, and data. "
                    "Note: messages are stored separately in the 'messages' field."
    )
    status: ExecutionStatus = Field(
        default=ExecutionStatus.RUNNING,
        description="Current execution status (running, interrupted, complete, error)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Framework-specific execution metadata. Common keys: "
                    "- next_steps (LangGraph): List of upcoming nodes "
                    "- config: Execution configuration "
                    "- interrupt_data: Details about interruption "
                    "- checkpoint_id: State checkpoint identifier "
                    "- created_at: Timestamp of state creation "
                    "- error: Error details if status is ERROR"
    )

    def is_complete(self) -> bool:
        """Check if execution is complete.

        Returns:
            bool: True if execution status is COMPLETE
        """
        return self.status == ExecutionStatus.COMPLETE

    def requires_input(self) -> bool:
        """Check if execution requires user input.

        Returns:
            bool: True if execution is interrupted and waiting for input
        """
        return self.status == ExecutionStatus.INTERRUPTED


class StateExtractor(ABC):
    """Abstract base for extracting specific data from framework-specific objects.

    StateExtractor provides a composable pattern for extracting different pieces
    of information from framework-specific state/session/snapshot objects.

    Plugins can create multiple specialized extractors (e.g., ValuesExtractor,
    MessagesExtractor, MetadataExtractor) to build up an ExecutionSnapshot object.
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
    """Abstract base for framework-specific agent execution state extraction.

    Subclasses must implement methods to convert framework-specific state
    representations to the unified ExecutionSnapshot format.

    The StateAdapter is responsible for:
    - Converting framework state snapshots to unified format
    - Extracting interrupt/pause information
    - Generating resume inputs from user feedback
    - Extracting messages and metadata from state
    """

    @abstractmethod
    def get_state_from_snapshot(self, snapshot: Any) -> ExecutionSnapshot:
        """Extract unified execution snapshot from a framework-specific state snapshot.

        Args:
            snapshot: A framework-specific state snapshot

        Returns:
            ExecutionSnapshot: The execution state in unified format

        Raises:
            ValueError: If snapshot cannot be converted
        """
        pass

    @abstractmethod
    def extract_interrupt_info(self, state: ExecutionSnapshot) -> Optional[InterruptInfo]:
        """Extract interrupt information from execution snapshot.

        Args:
            state: The execution snapshot to examine

        Returns:
            Optional[InterruptInfo]: Interrupt information if execution is interrupted,
                                    None otherwise
        """
        pass

    @abstractmethod
    def create_resume_input(self, user_input: Any, state: ExecutionSnapshot) -> dict[str, Any]:
        """Create framework-specific resume input from user feedback.

        Args:
            user_input: User's response/feedback after interruption
            state: The current execution snapshot

        Returns:
            dict[str, Any]: Resume input formatted for the framework

        Raises:
            ValueError: If resume input cannot be created
        """
        pass

