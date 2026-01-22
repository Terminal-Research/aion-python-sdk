import logging
import os
import sys

import uvicorn
from aion.shared.agent import agent_manager
from aion.shared.config import AgentConfig
from aion.shared.logging import get_logger
from aion.shared.logging.base import AionLogger
from aion.shared.utils.logging import replace_uvicorn_loggers, replace_logstash_loggers
from dotenv import load_dotenv

from aion.server.agent import AgentFactory
from aion.server.core.app import AppFactory
from aion.server.db import DbFactory, db_manager
from aion.server.plugins import PluginFactory
from aion.server.tasks import store_manager

# Set custom logger class globally for all loggers including uvicorn
logging.setLoggerClass(AionLogger)

logger = get_logger()

dotenv_path = load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), verbose=True)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


async def async_serve(
        agent_id: str,
        agent_config: AgentConfig,
        port: int,
        startup_callback=None,
        serialized_socket=None
):
    try:
        aion_agent = await agent_manager.create_agent(
            agent_id=agent_id,
            port=port,
            config=agent_config)

        # Create factories via dependency injection
        db_factory = DbFactory(db_manager=db_manager)
        agent_factory = AgentFactory(aion_agent=aion_agent)
        plugin_factory = PluginFactory(db_manager=db_manager)

        # Create and initialize AppFactory with all dependencies
        app_factory = await AppFactory(
            aion_agent=aion_agent,
            db_factory=db_factory,
            agent_factory=agent_factory,
            plugin_factory=plugin_factory,
            store_manager=store_manager,
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
            app=app_factory.get_fastapi_app(),
            host=aion_agent.host if sockets is None else None,
            port=aion_agent.port if sockets is None else None,
            log_config=None,
            access_log=False
        )
        # If we have sockets, we need to manually set them on the server
        server = uvicorn.Server(config=uconfig)
        if sockets is not None:
            server.servers = []  # Clear default servers

        await server.serve(sockets=sockets)

    except MissingAPIKeyError as ex:
        logger.error(f'Error: {ex}')
        exit(1)

    except Exception as ex:
        logger.error(f'An error occurred during server startup: {ex}')
        exit(1)


async def run_server(
        agent_id: str,
        agent_config: AgentConfig,
        port: int,
        startup_callback=None,
        serialized_socket=None
):
    """Starts the Currency Agent server."""
    try:
        # Configure custom loggers from uvicorn / logstash
        replace_uvicorn_loggers(suppress_startup_logs=True)
        replace_logstash_loggers()

        # RUN AGENT
        await async_serve(agent_id, agent_config, port, startup_callback, serialized_socket)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)

    except Exception as ex:
        logger.error(f"Fatal error: {ex}")
        sys.exit(1)
