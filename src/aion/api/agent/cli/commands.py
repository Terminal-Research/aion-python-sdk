"""
Command-line interface commands for AION Agent API.

This module implements the CLI commands for managing the AION Agent API server.
"""
import logging
import os
import pathlib
import shutil
import sys
from typing import Optional, Callable, Sequence

import click

from aion.api.agent.cli.exec import Runner, subp_exec
from aion.api.agent.cli.progress import Progress

from aion.api.agent.cli import config as cfg
from aion.api.agent.server import run_server

# Configure logging
logger = logging.getLogger(__name__)

OPT_PULL = click.option(
    "--pull/--no-pull",
    default=True,
    show_default=True,
    help="""
    Pull latest images. Use --no-pull for running the server with locally-built images.

    \b
    Example:
        aion serve --no-pull
    \b
    """,
)

OPT_CONFIG = click.option(
    "--config",
    "-c",
    help="""Path to configuration file declaring dependencies, graphs and environment variables.

    \b
    Config file must be a JSON file that has the following keys:
    - "dependencies": array of dependencies for langgraph API server. Dependencies can be one of the following:
      - ".", which would look for local python packages, as well as pyproject.toml, setup.py or requirements.txt in the app directory
      - "./local_package"
      - "<package_name>
    - "graphs": mapping from graph ID to path where the compiled graph is defined, i.e. ./your_package/your_file.py:variable, where
        "variable" is an instance of langgraph.graph.graph.CompiledGraph
    - "env": (optional) path to .env file or a mapping from environment variable to its value
    - "python_version": (optional) 3.11, 3.12, or 3.13. Defaults to 3.11
    - "pip_config_file": (optional) path to pip config file
    - "dockerfile_lines": (optional) array of additional lines to add to Dockerfile following the import from parent image

    \b
    Example:
        langgraph up -c langgraph.json

    \b
    Example:
    {
        "dependencies": [
            "langchain_openai",
            "./your_package"
        ],
        "graphs": {
            "my_graph_id": "./your_package/your_file.py:variable"
        },
        "env": "./.env"
    }

    \b
    Example:
    {
        "python_version": "3.11",
        "dependencies": [
            "langchain_openai",
            "."
        ],
        "graphs": {
            "my_graph_id": "./your_package/your_file.py:variable"
        },
        "env": {
            "OPENAI_API_KEY": "secret-key"
        }
    }

    Defaults to looking for langgraph.json in the current directory.""",
    default="langgraph.json",
    type=click.Path(
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
        path_type=pathlib.Path,
    ),
)

@click.group()
@click.version_option(version="0.1.0", prog_name="AION Agent API")
def cli():
    """Command-line interface for AION Agent API server."""
    pass


@cli.command(help="🚀 Launch AION Agent API server")
@OPT_CONFIG
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
            config_dict = cfg.validate_config_file(config)
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
        env=config_dict.get("env"),
        store=config_dict.get("store"),
        auth=config_dict.get("auth"),
        http=config_dict.get("http"),
        n_jobs_per_worker=n_jobs_per_worker,
    )


def _build(
    runner,
    set: Callable[[str], None],
    config: pathlib.Path,
    config_json: dict,
    base_image: Optional[str],
    pull: bool,
    tag: str,
    passthrough: Sequence[str] = (),
):
    
    if config_json.get("node_version"):
        raise NotImplementedError("Node.js is not supported")
    
    # TODO: we need a base image. For now, we only support python
    base_image = base_image or "langchain/langgraph-api" 

    # pull latest images
    if pull:
        runner.run(
            subp_exec(
                "docker",
                "pull",
                f"{base_image}:{config_json['python_version']}",
                verbose=True,
            )
        )
    set("Building...")
    # apply options
    args = [
        "-f",
        "-",  # stdin
        "-t",
        tag,
    ]
    # apply config
    stdin, additional_contexts = cfg.config_to_docker(
        config, config_json, base_image
    )
    # add additional_contexts
    if additional_contexts:
        additional_contexts_str = ",".join(
            f"{k}={v}" for k, v in additional_contexts.items()
        )
        args.extend(["--build-context", additional_contexts_str])
    # run docker build
    runner.run(
        subp_exec(
            "docker",
            "build",
            *args,
            *passthrough,
            str(config.parent),
            input=stdin,
            verbose=True,
        )
    )

@OPT_CONFIG
@OPT_PULL
@click.option(
    "--tag",
    "-t",
    help="""Tag for the docker image.

    \b
    Example:
        aion build -t my-image

    \b
    """,
    required=True,
)
@click.option(
    "--base-image",
    hidden=True,
)
@click.argument("docker_build_args", nargs=-1, type=click.UNPROCESSED)
@cli.command(
    help="📦 Build Aion API server Docker image.",
    context_settings=dict(
        ignore_unknown_options=True,
    ),
)
def build(
    config: pathlib.Path,
    docker_build_args: Sequence[str],
    base_image: Optional[str],
    pull: bool,
    tag: str,
):
    with Runner() as runner, Progress(message="Pulling...") as set:
        if shutil.which("docker") is None:
            raise click.UsageError("Docker not installed") from None
        config_json = cfg.validate_config_file(config)
        _build(
            runner, set, config, config_json, base_image, pull, tag, docker_build_args
        )


if __name__ == "__main__":
    cli()
