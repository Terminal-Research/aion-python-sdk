"""Events module for ADK plugin.

This module handles conversion of ADK events to unified ExecutionEvent format.
Extracts message content (text, thoughts) and tool calls/responses.
"""

from .converter import ADKEventConverter

__all__ = ["ADKEventConverter"]
