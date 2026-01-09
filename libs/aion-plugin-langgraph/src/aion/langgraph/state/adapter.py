"""LangGraph-specific state adapter for converting snapshots to ExecutionSnapshot.

This module provides the LangGraphStateAdapter for extracting and converting
state information from LangGraph StateSnapshot into the unified ExecutionSnapshot format.

Uses a composable extractor pattern to separate concerns:
- StateValuesExtractor: Extracts state dictionary
- MessagesExtractor: Extracts conversation messages
- MetadataExtractor: Extracts LangGraph-specific metadata and execution status
"""

from typing import Any, Optional

from aion.shared.agent.adapters import (
    ExecutionSnapshot,
    ExecutionStatus,
    InterruptInfo,
)
from aion.shared.logging import get_logger
from langgraph.types import Command, StateSnapshot

from .extractors import MessagesExtractor, MetadataExtractor, StateValuesExtractor

logger = get_logger()


class LangGraphStateAdapter:
    """Adapter for converting LangGraph StateSnapshot to unified ExecutionSnapshot.

    This adapter uses a composable extractor pattern to extract different pieces
    of information from StateSnapshots:
    - Values extractor: State dictionary (excluding messages)
    - Messages extractor: Conversation history
    - Metadata extractor: LangGraph-specific data (next_steps, interrupts, status)

    The extractors can be extended or replaced to customize state extraction.
    """

    def __init__(self):
        """Initialize adapter with extractors."""
        self._values_extractor = StateValuesExtractor()
        self._messages_extractor = MessagesExtractor()
        self._metadata_extractor = MetadataExtractor()

    def get_state_from_snapshot(self, snapshot: StateSnapshot) -> ExecutionSnapshot:
        """Convert LangGraph StateSnapshot to unified ExecutionSnapshot.

        Uses extractors to gather state, messages, and metadata, then
        combines them into a unified ExecutionSnapshot.

        Args:
            snapshot: LangGraph StateSnapshot object

        Returns:
            ExecutionSnapshot with state, messages, status, and metadata
        """
        # Extract state values (excluding messages)
        state = self._values_extractor.extract(snapshot)

        # Extract messages
        messages = self._messages_extractor.extract(snapshot)

        # Extract metadata (includes next_steps, interrupts, status)
        metadata = self._metadata_extractor.extract(snapshot)

        # Get execution status from metadata
        status = metadata.pop("execution_status", ExecutionStatus.COMPLETE)

        # Build ExecutionSnapshot
        execution_snapshot = ExecutionSnapshot(
            state=state,
            messages=messages,
            status=status,
            metadata=metadata,
        )

        return execution_snapshot

    @staticmethod
    def extract_all_interrupts(state: ExecutionSnapshot) -> list[InterruptInfo]:
        """Extract all interrupt information from ExecutionSnapshot.

        Converts LangGraph interrupt data into list of InterruptInfo objects.
        LangGraph 0.6.0+ supports multiple simultaneous interrupts.

        Args:
            state: ExecutionSnapshot to extract interrupt info from

        Returns:
            List of InterruptInfo objects (empty list if no interrupts)
        """
        if not state.requires_input():
            return []

        interrupt_data = state.metadata.get("interrupt_data")

        if not interrupt_data:
            logger.warning("State marked as interrupted but no interrupt_data found")
            return [InterruptInfo(
                id=None,
                value="Unknown interrupt",
                metadata={"error": "missing_interrupt_data"}
            )]

        # Handle list of interrupts (LangGraph 0.6.0+ supports multiple)
        if isinstance(interrupt_data, list):
            all_interrupts = []

            for idx, interrupt_item in enumerate(interrupt_data):
                if isinstance(interrupt_item, dict):
                    interrupt_id = interrupt_item.get("id", f"unknown-{idx}")
                    interrupt_value = interrupt_item.get("value", "Input required")

                    # Optional: Extract prompt from value if it's a string
                    prompt = None
                    if isinstance(interrupt_value, str):
                        prompt = interrupt_value
                    elif isinstance(interrupt_value, dict) and "prompt" in interrupt_value:
                        prompt = interrupt_value.get("prompt")

                    # Optional: Extract options from value if structured
                    options = None
                    if isinstance(interrupt_value, dict) and "options" in interrupt_value:
                        options = interrupt_value.get("options")

                    all_interrupts.append(InterruptInfo(
                        id=interrupt_id,
                        value=interrupt_value,
                        prompt=prompt,
                        options=options,
                        metadata=interrupt_item,
                    ))
                else:
                    # Fallback for unexpected item format
                    logger.warning(f"Unexpected interrupt item format at index {idx}: {type(interrupt_item)}")
                    all_interrupts.append(InterruptInfo(
                        id=f"fallback-{idx}",
                        value=interrupt_item,
                        metadata={"raw_data": interrupt_item, "error": "unexpected_format"},
                    ))

            return all_interrupts

        # Fallback for non-list format
        logger.warning(f"Unexpected interrupt_data format: {type(interrupt_data)}")
        return [InterruptInfo(
            id="fallback",
            value=interrupt_data,
            metadata={"raw_data": interrupt_data, "error": "unexpected_format"},
        )]

    @staticmethod
    def create_resume_input(
            user_input: Any,
            state: ExecutionSnapshot,
    ) -> Command:
        """Create LangGraph Command object for resuming execution.

        Args:
            user_input: User's response/feedback after interruption
            state: The current execution snapshot

        Returns:
            LangGraph Command object with resume data
        """
        return Command(resume=user_input)


__all__ = ["LangGraphStateAdapter"]
