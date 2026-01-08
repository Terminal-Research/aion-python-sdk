"""Events module for LangGraph plugin.

This module handles conversion of LangGraph events to unified ExecutionEvent format.
"""

from .converter import LangGraphEventConverter

__all__ = ["LangGraphEventConverter"]
