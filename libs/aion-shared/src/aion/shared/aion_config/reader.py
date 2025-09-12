from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union

import yaml
from pydantic import ValidationError

from aion.shared.utils import get_config_path
from .models import AgentConfig

logger = logging.getLogger(__name__)


class AionConfigReader:
    """Handles loading, parsing, and validation of Aion configuration files."""

    def __init__(self, config_path: Optional[Path] = None, logger_: logging.Logger | None = None):
        self.config_path = config_path or get_config_path()
        self.logger = logger_ or logging.getLogger(__name__)

    def load_config_file(self) -> Dict[str, Any]:
        """Load and parse the configuration file.

        Returns:
            Parsed configuration dictionary.

        Raises:
            ValueError: If the config file is invalid.
        """
        try:
            with self.config_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)

            if not isinstance(data, dict):
                raise ValueError("Configuration file must contain a mapping at the root")

            return data
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in configuration file: {e}") from e
        except Exception as e:
            raise ValueError(f"Failed to load configuration file: {e}") from e

    def extract_agents_config(self, config: Dict[str, Any]) -> Dict[str, Union[str, Dict[str, Any]]]:
        """Extract agents configuration from the main config.

        Args:
            config: The main configuration dictionary.

        Returns:
            Dictionary mapping agent IDs to their configurations.
        """
        aion_cfg = config.get("aion", {})
        agents_cfg = aion_cfg.get("agent", {}) or aion_cfg.get("graph", {})

        if not agents_cfg:
            self.logger.warning("No agents configured")
            return {}

        return agents_cfg

    def validate_config(self, config: Dict[str, Any]) -> None:
        """Validate the overall configuration structure.

        Args:
            config: Configuration dictionary to validate.

        Raises:
            ValueError: If the configuration is invalid.
        """
        if not isinstance(config, dict):
            raise ValueError("Configuration must be a dictionary")

        if 'aion' not in config:
            raise ValueError("Configuration must contain 'aion' section")

        aion_cfg = config['aion']
        if not isinstance(aion_cfg, dict):
            raise ValueError("'aion' section must be a dictionary")

        # Check that there's at least one agent configuration method
        has_agents = 'agent' in aion_cfg or 'graph' in aion_cfg
        if not has_agents:
            self.logger.warning("No 'agent' or 'graph' section found in configuration")

    @staticmethod
    def parse_agent_config(config_data: Dict[str, Any]) -> AgentConfig:
        """Parse and validate agent configuration using Pydantic.

        Args:
            config_data: Raw configuration dictionary.

        Returns:
            Validated AgentConfig instance.

        Raises:
            ValueError: If configuration is invalid.
        """
        try:
            return AgentConfig(**config_data)
        except ValidationError as e:
            raise ValueError(f"Invalid agent configuration: {e}") from e

    def load_and_validate_config(self) -> Dict[str, Union[str, Dict[str, Any]]]:
        """Load the configuration file and extract validated agents' configuration.

        Returns:
            Dictionary mapping agent IDs to their configurations.

        Raises:
            ValueError: If the configuration is invalid.
        """
        self.logger.info("Loading configuration from %s", self.config_path)

        # Load and validate config
        config = self.load_config_file()
        self.validate_config(config)

        # Extract and return agents configuration
        return self.extract_agents_config(config)
