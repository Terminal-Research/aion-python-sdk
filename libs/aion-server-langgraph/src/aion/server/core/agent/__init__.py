"""Core agent module for AION server.

This module provides the unified agent representation and management
for the AION server. It abstracts framework-specific details and provides
a consistent interface for working with agents.

Classes:
    AionAgent: Unified agent representation for all frameworks
    AgentMetadata: Agent runtime metadata (Pydantic model)
    AgentCapability: Enum of possible agent capabilities
    AgentManager: Singleton manager for the single agent instance
    ModuleLoader: Utility for loading Python modules from various path formats

Singletons:
    agent_manager: Global agent manager instance
"""

from aion.server.core.agent.agent import AionAgent
from aion.server.core.agent.manager import AgentManager, agent_manager
from aion.server.core.agent.models import AgentMetadata
from aion.server.core.agent.module_loader import ModuleLoader

__all__ = [
    "AionAgent",
    "AgentMetadata",
    "AgentManager",
    "agent_manager",
    "ModuleLoader",
]
