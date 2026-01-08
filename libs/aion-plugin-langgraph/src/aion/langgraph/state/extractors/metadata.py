"""Metadata extractor for LangGraph snapshots.

This module extracts LangGraph-specific metadata from StateSnapshot,
including next_steps, interrupts, timestamps, and configuration.
"""

from typing import Any, Dict, override

from aion.shared.agent.adapters import StateExtractor, ExecutionStatus
from aion.shared.logging import get_logger
from langgraph.types import StateSnapshot, Interrupt

logger = get_logger()


class MetadataExtractor(StateExtractor):
    """Extractor for LangGraph-specific metadata from StateSnapshot.

    Extracts:
    - next_steps: List of upcoming nodes to execute
    - interrupt_data: Information about any interrupts
    - created_at: Snapshot creation timestamp
    - parent_config: Parent configuration
    - execution_status: Whether interrupted or complete
    """

    @override
    def extract(self, snapshot: StateSnapshot) -> Dict[str, Any]:
        """Extract LangGraph-specific metadata from StateSnapshot.

        Args:
            snapshot: LangGraph StateSnapshot object

        Returns:
            Dict containing metadata fields
        """
        if not self.can_extract(snapshot):
            return {}

        metadata = {
            "langgraph_snapshot": True,
        }

        # Extract next steps
        if hasattr(snapshot, "next") and snapshot.next:
            metadata["next_steps"] = list(snapshot.next)
        else:
            metadata["next_steps"] = []

        # Extract timestamps
        if hasattr(snapshot, "created_at") and snapshot.created_at:
            metadata["created_at"] = snapshot.created_at

        # Extract parent config
        if hasattr(snapshot, "parent_config") and snapshot.parent_config:
            metadata["parent_config"] = snapshot.parent_config

        # Check for interrupts and extract interrupt data
        is_interrupted = self._has_interrupt(snapshot)
        if is_interrupted:
            interrupt_data = self._extract_interrupt_data(snapshot)
            if interrupt_data:
                metadata["interrupt_data"] = interrupt_data

        # Determine execution status
        metadata["execution_status"] = (
            ExecutionStatus.INTERRUPTED if is_interrupted else ExecutionStatus.COMPLETE
        )

        return metadata

    @override
    def can_extract(self, snapshot: StateSnapshot) -> bool:
        """Check if snapshot exists and can provide metadata.

        Args:
            snapshot: LangGraph StateSnapshot object

        Returns:
            bool: True if snapshot is valid
        """
        return snapshot is not None

    def _has_interrupt(self, snapshot: StateSnapshot) -> bool:
        """Check if snapshot contains any interrupts.

        StateSnapshot has a top-level 'interrupts' field containing
        all interrupts that occurred in this step.

        Args:
            snapshot: LangGraph StateSnapshot

        Returns:
            True if snapshot has interrupts
        """
        if not hasattr(snapshot, "interrupts"):
            return False

        interrupts = snapshot.interrupts
        return isinstance(interrupts, (list, tuple)) and len(interrupts) > 0

    def _extract_interrupt_data(self, snapshot: StateSnapshot) -> list | None:
        """Extract interrupt data from snapshot.

        Extracts interrupt information compatible with LangGraph 0.6.0+:
        - id: Unique interrupt identifier (new in 0.6.0, replaces interrupt_id)
        - value: Interrupt data/payload
        - Removed deprecated fields: when, resumable, ns

        Args:
            snapshot: LangGraph StateSnapshot

        Returns:
            List of interrupt data dicts with 'id' and 'value' keys, or None
        """
        if not hasattr(snapshot, "interrupts") or not snapshot.interrupts:
            return None

        all_interrupts = []

        for interrupt in snapshot.interrupts:
            if isinstance(interrupt, Interrupt):
                all_interrupts.append(
                    {
                        "id": interrupt.id,      # LangGraph 0.6.0+ unique ID
                        "value": interrupt.value,  # Interrupt payload/message
                    }
                )
            else:
                # Fallback for unknown interrupt types
                logger.warning(f"Unknown interrupt type: {type(interrupt)}")
                all_interrupts.append(interrupt)

        return all_interrupts if all_interrupts else None


__all__ = ["MetadataExtractor"]
