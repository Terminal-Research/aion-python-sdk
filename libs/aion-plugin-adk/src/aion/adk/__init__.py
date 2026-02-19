"""ADK Plugin for AION framework.

This plugin provides integration with Google's Agent Development Kit (ADK)
for building and running A2A (Agent-to-Agent) agents.

Architecture:
- adapter.py: Main agent adapter and orchestration
- plugin.py: Plugin entry point
- execution/: Agent execution and A2A event generation
- session/: Session management with pluggable backends (memory, database)
- state/: State extraction and conversion with specialized extractors
"""

from .adapter import ADKAdapter
from .execution import ADKExecutor
from .plugin import ADKPlugin
from .session import SessionServiceManager
from .state import StateConverter

__all__ = [
    "ADKPlugin",
    "ADKAdapter",
    "ADKExecutor",
    "SessionServiceManager",
    "StateConverter",
]
