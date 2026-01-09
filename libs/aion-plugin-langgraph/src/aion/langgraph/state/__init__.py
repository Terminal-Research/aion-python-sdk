"""State management module for LangGraph plugin.

This module handles state extraction, conversion, and checkpointing for LangGraph.

State extraction uses a composable extractor pattern:
- StateValuesExtractor: Extract state dictionary from snapshot.values
- MessagesExtractor: Extract conversation messages
- MetadataExtractor: Extract LangGraph-specific metadata (next_steps, interrupts)

The LangGraphStateAdapter combines these extractors to build unified ExecutionSnapshots.
"""

from .adapter import LangGraphStateAdapter
from .checkpointer import (
    Checkpoint,
    CheckpointerAdapter,
    CheckpointerConfig,
    CheckpointerType,
    LangGraphCheckpointerAdapter,
)
from .extractors import (
    MessagesExtractor,
    MetadataExtractor,
    StateValuesExtractor,
)

__all__ = [
    "LangGraphStateAdapter",
    "Checkpoint",
    "CheckpointerAdapter",
    "CheckpointerConfig",
    "CheckpointerType",
    "LangGraphCheckpointerAdapter",
    "StateValuesExtractor",
    "MessagesExtractor",
    "MetadataExtractor",
]
