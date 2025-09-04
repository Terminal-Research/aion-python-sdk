"""Server management command"""

import logging
import asyncclick as click

try:
    import structlog
    import aion.server.langgraph.logging
    logger = structlog.stdlib.get_logger(__name__)
except Exception:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)


@click.command(name="serve")
@click.option("--host", default="localhost", show_default=True, help="Server host")
@click.option("--port", default=10000, show_default=True, help="Server port")
async def serve(host: str, port: int) -> None:
    """🚀 Run the AION Agent API server"""
    logger.info(
        "Starting AION Agent API server",
        extra={"host": host, "port": port},
    )
    logger.info(welcome_message(host, port))

    try:
        from aion.server.langgraph.__main__ import main as server_main
    except Exception as exc:
        logger.error("Failed to import server", exc_info=exc)
        raise click.ClickException(
            "Unable to start server. Is aion-server-langgraph installed?"
        ) from exc

    # Note: If server_main.callback is not async, you might need to wrap it
    await server_main(host=host, port=port)


def welcome_message(host: str, port: int) -> str:
    """Return an ASCII welcome banner for the API server.

    Args:
        host: Hostname where the server is running.
        port: Port number where the server is running.

    Returns:
        A formatted multi-line string containing usage information.
    """
    return f"""

        Welcome to

╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩

- 🚀 API: http://{host}:{port}
- 📚 API Docs: http://{host}:{port}/docs
- 🖥️ Agent Card: http://{host}:{port}/.well-known/agent-card.json

This server provides endpoints for LangGraph agents.

"""
