"""
Configuration utilities for the AION Agent API CLI.

This module provides utilities for loading and validating configuration files.
"""

import json
import os
import pathlib
import logging
from typing import Any, Dict, Optional, Union, Mapping

logger = logging.getLogger(__name__)


def validate_config_file(config_path: pathlib.Path) -> Dict[str, Any]:
    """
    Validate and load the configuration file.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        Dictionary containing the validated configuration
        
    Raises:
        ValueError: If the configuration file is invalid
    """
    if not config_path.exists():
        raise ValueError(f"Configuration file does not exist: {config_path}")
    
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse configuration file {config_path}: {e}")
    
    # Validate required fields
    if not isinstance(config, dict):
        raise ValueError(f"Configuration must be a JSON object: {config_path}")
    
    # Validate graphs if present
    if "graphs" in config and not isinstance(config["graphs"], dict):
        raise ValueError("'graphs' must be a mapping from graph ID to path or object")
    
    # Validate environment settings
    if "env" in config:
        if isinstance(config["env"], str):
            env_path = pathlib.Path(config["env"])
            if not env_path.exists():
                logger.warning(f"Environment file does not exist: {env_path}")
        elif not isinstance(config["env"], dict):
            raise ValueError("'env' must be either a path to an env file or a dictionary of environment variables")
    
    # Return the validated configuration
    return config

def load_env_from_config(config: Dict[str, Any]) -> Optional[Union[str, Dict[str, str]]]:
    """
    Load environment variables from the configuration.
    
    This function first looks for a template .env file to use as a base,
    then overrides values from the configuration file.
    
    Args:
        config: The configuration dictionary
        
    Returns:
        Environment variables or path to .env file
    """
    # Start with default environment variables
    env_vars = {}
    
    # First try to load the .env.template file as a base configuration
    template_path = pathlib.Path(__file__).parent.parent.parent.parent.parent / ".env.template"
    if template_path.exists():
        try:
            # Parse the .env.template file
            with open(template_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env_vars[key] = value
                        
            logger.debug(f"Loaded base environment from {template_path}")
        except Exception as e:
            logger.warning(f"Failed to load template environment: {e}")
    
    # Then override with configuration from langgraph.json
    if "env" in config:
        if isinstance(config["env"], str):
            # If env is a string, it's a path to an env file
            # @todo DJ if this is a path to a file, we should update the env variables with its contents
            return config["env"]
        elif isinstance(config["env"], dict):
            # If env is a dict, merge it with our base environment
            env_vars.update(config["env"])
    
    # Return the merged environment variables
    return env_vars if env_vars else None
