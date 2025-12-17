"""ADK Plugin for AION framework.

This plugin provides integration with Google's Agent Development Kit (ADK)
for building and running A2A (Agent-to-Agent) agents.

Architecture:
- adapter.py: Main agent adapter and orchestration
- plugin.py: Plugin entry point
- execution/: Agent execution and streaming
- session/: Session management with pluggable backends (memory, database)
- state/: State extraction and conversion with specialized extractors
- events/: Event conversion with specialized handlers
"""

from .adapter import ADKAdapter
from .events import ADKEventConverter
from .execution import ADKExecutor
from .plugin import ADKPlugin
from .session import SessionServiceManager
from .state import StateConverter

__all__ = [
    "ADKPlugin",
    "ADKAdapter",
    "ADKExecutor",
    "ADKEventConverter",
    "SessionServiceManager",
    "StateConverter",
]
