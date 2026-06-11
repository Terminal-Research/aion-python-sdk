"""Configuration collectors — abstract base and agent configuration collector."""

from .base import BaseCollector
from .agent_configuration import AgentConfigurationCollector

__all__ = ["BaseCollector", "AgentConfigurationCollector"]
