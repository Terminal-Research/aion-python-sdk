import os
import sys
import logging

import uvicorn
from aion.shared.aion_config import AgentConfig
from aion.shared.logging import get_logger
from aion.shared.logging.base import AionLogger
from aion.shared.settings import app_settings
from aion.shared.utils.logging import replace_uvicorn_loggers, replace_logstash_loggers
from dotenv import load_dotenv

from aion.server.core.app import AppFactory, AppContext
from aion.server.db import db_manager
from aion.server.tasks import store_manager

# Set custom logger class globally for all loggers including uvicorn
logging.setLoggerClass(AionLogger)

logger = get_logger()

dotenv_path = load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), verbose=True)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


async def async_serve(agent_id: str, agent_config: AgentConfig, port: int, startup_callback=None, serialized_socket=None):
    try:
        app_settings.set_app_port(port)
        app_settings.set_agent_config(agent_id=agent_id, agent_config=agent_config)

        app_factory = await AppFactory(
            agent_id=agent_id,
            agent_config=agent_config,
            port=port,
            context=AppContext(
                db_manager=db_manager,
                store_manager=store_manager
            ),
            startup_callback=startup_callback
        ).initialize()

        if not app_factory:
            return

        # Deserialize socket if provided
        sockets = None
        if serialized_socket is not None:
            from aion.shared.utils.ports.reservation import deserialize_socket
            sock = deserialize_socket(serialized_socket)
            sockets = [sock]
            logger.debug(f"Using passed socket for agent '{agent_id}' on port {port}")

        uconfig = uvicorn.Config(
            app=app_factory.get_starlette_app(),
            host=app_factory.get_agent_host() if sockets is None else None,
            port=port if sockets is None else None,
            log_config=None,
            access_log=False
        )

        # If we have sockets, we need to manually set them on the server
        server = uvicorn.Server(config=uconfig)
        if sockets is not None:
            server.servers = []  # Clear default servers

        await server.serve(sockets=sockets)

    except MissingAPIKeyError as e:
        logger.error(f'Error: {e}')
        exit(1)

    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        exit(1)


async def run_server(agent_id: str, agent_config: AgentConfig, port: int, startup_callback=None, serialized_socket=None):
    """Starts the Currency Agent server."""
    try:
        # CONFIGURE CUSTOM LOGGERS FOR UVICORN / LOGSTASH
        replace_uvicorn_loggers(suppress_startup_logs=True)
        replace_logstash_loggers()
        # RUN AGENT
        await async_serve(agent_id, agent_config, port, startup_callback, serialized_socket)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    run_server()
