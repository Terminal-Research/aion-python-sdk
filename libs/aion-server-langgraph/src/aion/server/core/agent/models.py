"""Data models for agent representation.

This module contains Pydantic models used by the agent system.
"""
from dataclasses import dataclass

from pydantic import Field


@dataclass
class AgentMetadata:
    """Runtime agent metadata.

    This model stores runtime information about the agent that is not
    part of the static configuration (AgentConfig). For static information
    like framework, version, name, etc., use agent.config instead.

    Attributes:
        created_at: Unix timestamp when agent was created
    """

    created_at: float = Field(
        ...,
        description="Unix timestamp when agent was created"
    )
