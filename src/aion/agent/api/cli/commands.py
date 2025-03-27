"""
Command-line interface commands for AION Agent API.

This module implements the CLI commands for managing the AION Agent API server.
"""

import logging
import pathlib
import sys
from typing import Any, Dict, Optional

import click

from aion.agent.api.cli.config import (
    validate_config_file,
    load_graphs_from_config,
    load_env_from_config,
)
from aion.agent.api.server import run_server
from aion.agent.api.server.config import get_config

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
def serve(
    config: pathlib.Path,
    host: Optional[str],
    port: Optional[int],
    reload: Optional[bool],
    env_file: Optional[str],
    open_browser: Optional[bool],
    debug_port: Optional[int],
    wait_for_client: Optional[bool],
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
    
    # Load graphs from config
    graphs = load_graphs_from_config(config_dict)
    
    # Run the server with the consolidated settings
    run_server(
        host=server_host,
        port=server_port,
        reload=server_reload,
        graphs=graphs,
        env=server_env,
        open_browser=server_open_browser,
        debug_port=server_debug_port,
        wait_for_client=server_wait_for_client,
    )


@cli.command(help="📖 Show help information about the AION Agent API")
def info():
    """
    Display information about the AION Agent API server.
    
    Shows the current configuration settings and available commands.
    """
    config = get_config()
    
    click.echo("\nAION Agent API Configuration:")
    click.echo(f"  Host: {config.host}")
    click.echo(f"  Port: {config.port}")
    click.echo(f"  Debug Mode: {'Enabled' if config.debug else 'Disabled'}")
    click.echo("\nFor more information, run 'aion-agent-api serve --help'")


if __name__ == "__main__":
    cli()
