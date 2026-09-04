"""Server-side plugin initialization and lifecycle management.

This module provides plugin orchestration, lifecycle management, and integration
with the server's systems. It builds on top of the shared plugin protocols and
registry to provide full plugin lifecycle support.
"""

from .factory import PluginFactory
from .base import BasePluginProtocol
from .agent import AgentPluginProtocol
from .registry import PluginRegistry

__all__ = [
    "PluginFactory",
    "BasePluginProtocol",
    "AgentPluginProtocol",
    "PluginRegistry",
]
