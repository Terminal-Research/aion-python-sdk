"""Command-line interface for the Aion Python SDK."""

from __future__ import annotations

import logging

import click

__version__ = "0.1.0"

try:  # pragma: no cover - optional dependency may not be installed
    import structlog

    import aion.server.langgraph.logging  # noqa: F401 - triggers global configuration

    logger = structlog.stdlib.get_logger(__name__)
except Exception:  # pragma: no cover - structlog is optional for tests
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version=__version__, prog_name="Aion SDK")
def cli() -> None:
    """Command line interface for the Aion Python SDK."""


@cli.command(help="🚀 Run the AION Agent API server")
@click.option("--host", default="localhost", show_default=True, help="Server host")
@click.option("--port", default=10000, show_default=True, help="Server port")
def serve(host: str, port: int) -> None:
    """Run the example AION Agent API server."""
    logger.info(
        "Starting AION Agent API server",
        extra={"host": host, "port": port},
    )
    logger.info(welcome_message(host, port))
    try:
        from aion.server.langgraph.__main__ import main as server_main
    except Exception as exc:  # pragma: no cover - optional dependency may fail
        logger.error("Failed to import server", exc_info=exc)
        raise click.ClickException(
            "Unable to start server. Is aion-server-langgraph installed?"
        ) from exc

    server_main.callback(host=host, port=port)
    
def welcome_message(host: str, port: int):
    return """

        Welcome to

╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩

- 🚀 API: http://{host}:{port}
- 📚 API Docs: http://{host}:{port}/docs
- 🖥️ Agent Card: http://{host}:{port}/.well-known/agent.json

This server provides endpoints for LangGraph agents.

"""


if __name__ == "__main__":
    cli()

