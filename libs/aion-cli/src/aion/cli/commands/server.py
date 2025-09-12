"""Server management command"""

import logging
import asyncclick as click
from aion.server.configs import app_settings
from aion.server.langgraph.agent import AionConfigReader
from aion.shared.utils import substitute_vars
from aion.server.utils.constants import SPECIFIC_AGENT_CARD_WELL_KNOWN_PATH

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
    app_settings.update_serve_settings(host=host, port=port)

    logger.info(welcome_message())

    try:
        from aion.server import run_server
    except Exception as exc:
        logger.error("Failed to import server", exc_info=exc)
        raise click.ClickException(
            "Unable to start server. Is aion-server-langgraph installed?"
        ) from exc

    # Note: If server_main.callback is not async, you might need to wrap it
    await run_server(host=host, port=port)


def welcome_message() -> str:
    """Return an ASCII welcome banner for the API server.

    Returns:
        A formatted multi-line string containing usage information.
    """
    try:
        graph_ids = list(AionConfigReader().load_and_validate_config().keys())
    except Exception:
        graph_ids = []

    if not graph_ids:
        agent_card_info = f"- 🖥️ Agent Card: ---"
    else:
        agent_card_info = "- 🖥️ Agent Cards:\n" + "\n".join(
            "  • {graph_id}: {url}".format(
                graph_id=graph_id,
                url=app_settings.url
                    + substitute_vars(
                    template=SPECIFIC_AGENT_CARD_WELL_KNOWN_PATH,
                    values={"graph_id": graph_id},
                ),
            )
            for graph_id in graph_ids
        )

    return f"""

    Welcome to

╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩

- 🚀 API: {app_settings.url}
- 📚 API Docs: {app_settings.url}/docs
{agent_card_info}

This server provides endpoints for LangGraph agents.

"""
