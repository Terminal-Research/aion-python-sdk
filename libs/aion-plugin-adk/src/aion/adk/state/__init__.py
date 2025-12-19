"""State management module for ADK plugin.

This module handles state extraction and conversion from ADK sessions
to the unified ExecutionSnapshot format.
"""

from .converter import StateConverter

__all__ = ["StateConverter"]
