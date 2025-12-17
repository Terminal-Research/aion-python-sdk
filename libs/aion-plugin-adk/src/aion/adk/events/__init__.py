"""Events module for ADK plugin.

This module handles conversion of ADK events to unified ExecutionEvent format
using specialized handlers for different event types.
"""

from .converter import ADKEventConverter

__all__ = ["ADKEventConverter"]
