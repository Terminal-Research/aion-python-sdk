"""Command-line interface for the Aion Python SDK."""

from __future__ import annotations

import logging

import click

__version__ = "0.1.0"

try:  # pragma: no cover - optional dependency may not be installed
    import structlog

    import aion_agent_api.logging  # noqa: F401 - triggers global configuration

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
def serve() -> None:
    """Stub for running the AION Agent API server."""
    welcome = """

        Welcome to

╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩

- 🚀 API: http://127.0.0.1:8000
- 📚 API Docs: http://127.0.0.1:8000/docs
- 🖥️ Admin Interface: http://127.0.0.1:8000/api/admin

This server provides endpoints for LangGraph agents.

"""
    logger.info(welcome)
    logger.info(
        "Patching langgraph_api", extra={"api_variant": "local_dev", "thread_name": "MainThread"}
    )


if __name__ == "__main__":
    cli()

