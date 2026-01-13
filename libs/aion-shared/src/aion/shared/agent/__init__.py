"""AION Agent module.

Provides the unified agent representation and management for the AION SDK.
The agent is the core of the SDK for processing requests through A2A protocol.
"""

from .aion_agent import AionAgent, agent_manager

__all__ = [
    "AionAgent",
    "agent_manager",
]
