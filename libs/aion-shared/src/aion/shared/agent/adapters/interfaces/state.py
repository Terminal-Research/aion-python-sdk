"""Unified interfaces for agent execution state management.

This module provides classes and interfaces for representing and managing
agent execution state in a framework-agnostic way.

Key classes:
- ExecutionSnapshot: Unified snapshot of agent execution state at a point in time
- ExecutionStatus: Status of agent execution (running, interrupted, complete, error)
- InterruptInfo: Information about execution interrupts/pauses
- StateExtractor: Base class for extracting data from framework-specific objects

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
    """Information about an agent execution interrupt/pause.

    Universal interrupt structure supporting multiple AI frameworks:
    - Core fields (id, value): Align with LangGraph 0.6.0+
    - Optional fields (prompt, options): Convenience for structured frameworks
      like AutoGen, CrewAI, Semantic Kernel

    Framework adapters decide whether to populate optional convenience fields.
    UI layers should check optional fields first, then fall back to parsing 'value'.
    """

    id: Optional[str] = Field(
        default=None,
        description="Unique interrupt identifier. Used to resume execution. "
                    "LangGraph: from Interrupt.id. AutoGen/CrewAI: generated or task_id."
    )
    value: Any = Field(
        description="Interrupt data/payload. Can be string, dict, or any structure. "
                    "LangGraph: from Interrupt.value. AutoGen: full message object. "
                    "Framework adapters may extract structured data into optional fields."
    )
    prompt: Optional[str] = Field(
        default=None,
        description="User-facing prompt/question (optional convenience field). "
                    "Extracted from 'value' by framework adapter if available. "
                    "Useful for frameworks that provide explicit prompts (AutoGen, CrewAI, SK)."
    )
    options: Optional[list[str]] = Field(
        default=None,
        description="Available response options (optional convenience field). "
                    "Extracted from 'value' by framework adapter if available. "
                    "Useful for frameworks that provide explicit choices (AutoGen, SK)."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional framework-specific interrupt metadata "
                    "(sender, context, timestamp, etc.)"
    )

    def get_prompt_text(self) -> str:
        """Extract user-facing prompt text from this interrupt.

        Priority:
        1. prompt field (if set by framework adapter)
        2. value field (if it's a string)
        3. Fallback message with interrupt id

        Returns:
            str: User-facing prompt text
        """
        if self.prompt:
            return self.prompt
        elif isinstance(self.value, str):
            return self.value
        else:
            return f"Agent requires input"


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
