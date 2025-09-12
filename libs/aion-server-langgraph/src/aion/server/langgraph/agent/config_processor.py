from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union

from aion.shared.aion_config import AgentConfig, AionConfigReader
from aion.shared.utils import get_config_path

from .base import BaseAgent
from .factory import AgentFactory

logger = logging.getLogger(__name__)


class AgentConfigProcessor:
    """Facade class that combines config reading and agent creation functionality.

    This class maintains backward compatibility while providing a clean interface
    for loading and processing agent configurations.
    """

    def __init__(self, config_path: Optional[Path] = None, logger_: logging.Logger | None = None):
        self.config_path = config_path or get_config_path()
        self.logger = logger_ or logging.getLogger(__name__)

        # Initialize the specialized components
        self.config_reader = AionConfigReader(self.config_path, logger)
        self.agent_factory = AgentFactory(self.config_path, logger)

    def load_and_process_config(self) -> Dict[str, BaseAgent]:
        """Load the configuration file and process all agents.

        Returns:
            Dictionary mapping agent IDs to BaseAgent instances.
        """
        # Load and validate configuration
        agents_config = self.config_reader.load_and_validate_config()

        if not agents_config:
            return {}

        # Create all agents
        return self.agent_factory.create_all_agents(agents_config, self.config_reader)

    # Delegate methods for backward compatibility
    def load_config_file(self) -> Dict[str, Any]:
        """Load and parse the configuration file."""
        return self.config_reader.load_config_file()

    def extract_agents_config(self, config: Dict[str, Any]) -> Dict[str, Union[str, Dict[str, Any]]]:
        """Extract agents configuration from the main config."""
        return self.config_reader.extract_agents_config(config)

    def validate_config(self, config: Dict[str, Any]) -> None:
        """Validate the overall configuration structure."""
        self.config_reader.validate_config(config)

    def parse_agent_config(self, config_data: Dict[str, Any]) -> AgentConfig:
        """Parse and validate agent configuration using Pydantic."""
        return self.config_reader.parse_agent_config(config_data)

    def process_agent_config(self, agent_id: str, config: Union[str, Dict[str, Any]]) -> BaseAgent:
        """Process a single agent configuration and return an agent instance."""
        return self.agent_factory.create_agent(agent_id, config, self.config_reader)

    def process_all_agents(self, agents_config: Dict[str, Union[str, Dict[str, Any]]]) -> Dict[str, BaseAgent]:
        """Process all agent configurations and return agent instances."""
        return self.agent_factory.create_all_agents(agents_config, self.config_reader)
