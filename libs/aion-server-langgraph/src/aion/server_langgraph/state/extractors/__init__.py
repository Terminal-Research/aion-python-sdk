"""State extractors for LangGraph plugin.

This module provides extractors for retrieving information from LangGraph StateSnapshots:
- StateValuesExtractor: Extract state values from snapshot.values (excluding messages)
- MessagesExtractor: Extract conversation messages from snapshot.values['messages']
- MetadataExtractor: Extract LangGraph-specific metadata (next_steps, interrupts, etc.)

New extractors can be added by implementing the StateExtractor interface
from aion.shared.agent.adapters.
"""

from .messages import MessagesExtractor
from .metadata import MetadataExtractor
from .values import StateValuesExtractor

__all__ = [
    "StateValuesExtractor",
    "MessagesExtractor",
    "MetadataExtractor",
]
