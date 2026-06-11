"""Configuration file reader and parser for Aion agents.

Handles loading, parsing, and validation of YAML configuration files
using Pydantic models.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import ValidationError

from aion.core.logging import get_logger
from aion.core.utils.path import get_config_path

from .exceptions import ConfigurationError
from .models import AgentConfig, AionConfig


class AionConfigReader:
    """Handles loading, parsing, and validation of Aion configuration files."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        logger_: Optional[logging.Logger] = None,
    ):
        self.config_path = config_path or get_config_path()
        self.logger = logger_ or get_logger()

    def load_config_file(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise ConfigurationError(
                "Configuration file not found",
                details=f"File path: {self.config_path}",
                file_path=self.config_path,
            )

        try:
            with self.config_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)

            if data is None:
                raise ConfigurationError(
                    "Configuration file is empty", file_path=self.config_path
                )

            if not isinstance(data, dict):
                raise ConfigurationError(
                    "Configuration file must contain a YAML mapping (dictionary) at the root level",
                    details=f"Found: {type(data).__name__}",
                    file_path=self.config_path,
                )

            return data

        except yaml.YAMLError as e:
            raise ConfigurationError(
                "Invalid YAML syntax in configuration file",
                details=str(e),
                file_path=self.config_path,
            ) from e
        except UnicodeDecodeError as e:
            raise ConfigurationError(
                "Configuration file encoding error",
                details=f"Expected UTF-8 encoding. Error: {e}",
                file_path=self.config_path,
            ) from e
        except Exception as e:
            raise ConfigurationError(
                "Failed to read configuration file",
                details=str(e),
                file_path=self.config_path,
            ) from e

    def _normalize_config_structure(
        self, config_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        if "aion" not in config_data:
            raise ConfigurationError(
                "Configuration file must contain an 'aion' section at the root level",
                details="Expected structure: aion:\n  deployment:\n    ...\n  agents:\n    ...",
                file_path=self.config_path,
            )

        self.logger.debug("Found 'aion' key in config, extracting nested configuration")
        aion_data = config_data["aion"]

        if not isinstance(aion_data, dict):
            raise ConfigurationError(
                "The 'aion' section must be a dictionary",
                details=f"Found: {type(aion_data).__name__}",
                file_path=self.config_path,
            )

        return aion_data

    def _format_pydantic_error(self, error: ValidationError) -> str:
        error_messages = []

        for err in error.errors():
            location = " -> ".join(str(loc) for loc in err["loc"])
            error_type = err["type"]
            message = err["msg"]

            if error_type == "missing":
                readable_msg = f"Required field '{location}' is missing"
            elif error_type == "value_error":
                readable_msg = f"Invalid value for '{location}': {message}"
            elif error_type == "type_error":
                readable_msg = f"Wrong type for '{location}': {message}"
            elif error_type == "enum":
                readable_msg = f"Invalid enum value for '{location}': {message}"
            elif "greater_than" in error_type:
                readable_msg = f"Value for '{location}' must be greater than specified minimum: {message}"
            elif "less_than" in error_type:
                readable_msg = f"Value for '{location}' must be less than specified maximum: {message}"
            else:
                readable_msg = f"Validation error for '{location}': {message}"

            error_messages.append(readable_msg)

        return "\n".join(error_messages)

    def validate_and_parse_config(self, config_data: Dict[str, Any]) -> AionConfig:
        try:
            normalized_data = self._normalize_config_structure(config_data)
            self.logger.debug(
                "Validating configuration with keys: %s", list(normalized_data.keys())
            )
            return AionConfig(**normalized_data)

        except ValidationError as e:
            formatted_error = self._format_pydantic_error(e)
            raise ConfigurationError(
                "Configuration validation failed",
                details=formatted_error,
                file_path=self.config_path,
            ) from e
        except Exception as e:
            raise ConfigurationError(
                "Unexpected error during configuration validation",
                details=str(e),
                file_path=self.config_path,
            ) from e

    def load_and_validate_config(self) -> AionConfig:
        self.logger.info("Loading configuration from %s", self.config_path)

        try:
            config_data = self.load_config_file()
            aion_config = self.validate_and_parse_config(config_data)
            self.logger.info(
                "Successfully loaded configuration with %d agents",
                len(aion_config.agents),
            )
            return aion_config

        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(
                "Unexpected error while loading configuration",
                details=str(e),
                file_path=self.config_path,
            ) from e

    def validate_agent_config(self, agent_data: Dict[str, Any]) -> AgentConfig:
        try:
            return AgentConfig(**agent_data)
        except ValidationError as e:
            formatted_error = self._format_pydantic_error(e)
            raise ConfigurationError(
                "Agent configuration validation failed", details=formatted_error
            ) from e
