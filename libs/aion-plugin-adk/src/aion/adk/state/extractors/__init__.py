"""State extractors for ADK plugin.

This module provides extractors for retrieving information from ADK sessions:
- StateValuesExtractor: Extract state values from session.state
- MessagesExtractor: Extract conversation messages from session.events

New extractors can be added by implementing the StateExtractor interface
from aion.shared.agent.adapters.
"""

from .messages import MessagesExtractor
from .values import StateValuesExtractor

__all__ = [
    "StateValuesExtractor",
    "MessagesExtractor",
]
