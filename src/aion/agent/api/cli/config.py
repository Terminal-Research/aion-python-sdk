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


def load_graphs_from_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load LangGraph instances from the configuration.
    
    Args:
        config: The configuration dictionary
        
    Returns:
        Dictionary mapping graph IDs to LangGraph instances
    """
    graphs = {}
    
    if "graphs" not in config:
        return graphs
    
    for graph_id, graph_path in config["graphs"].items():
        try:
            # Handle either a string path or a direct object
            if isinstance(graph_path, str):
                module_path, variable_name = graph_path.split(":")
                
                # Convert relative path to absolute and handle dot notation
                if module_path.startswith("./"):
                    module_path = module_path[2:]
                module_path = module_path.replace("/", ".")
                if module_path.endswith(".py"):
                    module_path = module_path[:-3]
                
                # Import the module and get the graph
                import importlib
                module = importlib.import_module(module_path)
                graph = getattr(module, variable_name)
                
                graphs[graph_id] = graph
                logger.info(f"Loaded graph '{graph_id}' from {graph_path}")
            else:
                # Assume it's a direct graph instance
                graphs[graph_id] = graph_path
                logger.info(f"Using provided graph instance for '{graph_id}'")
        except Exception as e:
            logger.warning(f"Failed to load graph '{graph_id}' from {graph_path}: {e}")
    
    return graphs


def load_env_from_config(config: Dict[str, Any]) -> Optional[Union[str, Dict[str, str]]]:
    """
    Load environment variables from the configuration.
    
    Args:
        config: The configuration dictionary
        
    Returns:
        Environment variables or path to .env file
    """
    if "env" not in config:
        return None
    
    return config["env"]
