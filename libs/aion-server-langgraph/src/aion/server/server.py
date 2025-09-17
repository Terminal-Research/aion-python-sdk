import logging
import os
import sys

import uvicorn
from aion.shared.aion_config import AgentConfig
from dotenv import load_dotenv

from aion.server.configs import app_settings
from aion.server.core.app import AppFactory

logger = logging.getLogger(__name__)

dotenv_path = load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), verbose=True)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


async def async_serve(agent_id: str, agent_config: AgentConfig):
    try:
        app_factory = await AppFactory.create_and_initialize(agent_id, agent_config)
        if not app_factory:
            return

        uconfig = uvicorn.Config(
            app=app_factory.get_starlette_app(),
            host=app_factory.get_agent_host(),
            port=app_factory.get_agent_config().port)
        server = uvicorn.Server(config=uconfig)

        await server.serve()

    except MissingAPIKeyError as e:
        logger.error(f'Error: {e}')
        exit(1)

    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        exit(1)


async def run_server(agent_id: str, agent_config: AgentConfig):
    """Starts the Currency Agent server."""
    try:
        await async_serve(agent_id, agent_config)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    run_server()
