"""
Command-line interface commands for AION Agent API.

This module implements the CLI commands for managing the AION Agent API server.
"""



import logging
import os
import pathlib
import sys
from typing import Optional

import click

from aion.api.agent.cli.config import (
    validate_config_file,
    load_env_from_config,
)
from aion.api.agent.server import run_server

# Configure logging
logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version="0.1.0", prog_name="AION Agent API")
def cli():
    """Command-line interface for AION Agent API server."""
    pass


@cli.command(help="🚀 Launch AION Agent API server")
@click.option(
    "--config",
    "-c",
    default="langgraph.json",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True, path_type=pathlib.Path),
    help="Path to configuration file declaring dependencies, graphs and environment variables",
)
@click.option(
    "--host",
    default=None,
    help="Host address to bind to. Use 0.0.0.0 to make the server publicly accessible. Overrides config file.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Port to listen on. Overrides config file.",
)
@click.option(
    "--reload/--no-reload",
    default=None,
    help="Enable auto-reload on code changes for development. Overrides config file.",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Path to .env file with environment variables. Overrides config file.",
)
@click.option(
    "--open-browser/--no-browser",
    default=None,
    help="Open a browser window after server starts. Overrides config file.",
)
@click.option(
    "--debug-port",
    type=int,
    help="Enable remote debugging by listening on the specified port. Overrides config file.",
)
@click.option(
    "--wait-for-client",
    is_flag=True,
    default=None,
    help="Wait for a debugger client to connect before starting the server. Overrides config file.",
)
@click.option(
    "--n-jobs-per-worker",
    type=int,
    default=None,
    help="Number of jobs to run per worker for graph execution. Overrides config file.",
)
def serve(
    config: pathlib.Path,
    host: Optional[str],
    port: Optional[int],
    reload: Optional[bool],
    env_file: Optional[str],
    open_browser: Optional[bool],
    debug_port: Optional[int],
    wait_for_client: Optional[bool],
    n_jobs_per_worker: Optional[int],
):
    """
    Launch the AION Agent API server.
    
    Uses configuration from the specified config file, with command-line options
    overriding the file settings when provided.
    
    Config file must be a JSON file with a structure similar to langgraph.json:
    {
        "host": "127.0.0.1",
        "port": 8000,
        "reload": false,
        "graphs": {
            "my_graph_id": "./my_package/my_module.py:graph_variable"
        },
        "env": "./.env" or {"KEY": "VALUE"}
    }
    """
    # Load configuration from file if it exists
    config_dict = {}
    if config.exists():
        try:
            config_dict = validate_config_file(config)
            logger.info(f"Using configuration from {config}")
        except ValueError as e:
            logger.warning(f"Error in configuration file: {e}")
            logger.info("Continuing with default/command-line settings...")
    else:
        logger.warning(f"Configuration file not found: {config}")
        logger.info("Using default/command-line settings...")
    
    # Get settings from config, with command-line options taking precedence
    server_host = host if host is not None else config_dict.get("host", "127.0.0.1")
    server_port = port if port is not None else config_dict.get("port", 8000)
    server_reload = reload if reload is not None else config_dict.get("reload", False)
    server_debug_port = debug_port if debug_port is not None else config_dict.get("debug_port")
    server_wait_for_client = wait_for_client if wait_for_client is not None else config_dict.get("wait_for_client", False)
    server_open_browser = open_browser if open_browser is not None else config_dict.get("open_browser", False)
    
    # Load environment from either command-line or config
    server_env = env_file if env_file else load_env_from_config(config_dict)
    
    cwd = os.getcwd()
    sys.path.append(cwd)
    dependencies = config_dict.get("dependencies", [])
    for dep in dependencies:
        dep_path = pathlib.Path(cwd) / dep
        if dep_path.is_dir() and dep_path.exists():
            logger.info(f"Adding dependency to sys.path: {dep_path}")
            sys.path.append(str(dep_path))
    
    # Note about graph configuration
    if "graphs" in config_dict:
        logger.info(
            "Graph configuration detected in config file. "
            "The LangGraph API will load graphs directly from the configuration."
        )
    
    run_server(
        host=server_host,
        port=server_port,
        reload=server_reload,
        graphs=config_dict.get("graphs", {}),
        open_browser=server_open_browser,
        debug_port=server_debug_port,
        wait_for_client=server_wait_for_client,
        env=server_env,
        store=config_dict.get("store"),
        auth=config_dict.get("auth"),
        http=config_dict.get("http"),
        n_jobs_per_worker=n_jobs_per_worker,
    )


# Info command removed to avoid premature imports


if __name__ == "__main__":
    cli()
