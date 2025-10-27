import logging
import os
import sys

import uvicorn
from aion.shared.config import AgentConfig
from aion.shared.logging import get_logger
from aion.shared.logging.base import AionLogger
from aion.shared.utils import replace_uvicorn_loggers, replace_logstash_loggers
from dotenv import load_dotenv

from aion.server.agent import register_available_adapters
from aion.shared.agent import agent_manager
from aion.server.core.app import AppFactory, AppContext
from aion.server.db import db_manager
from aion.server.tasks import store_manager

# Set custom logger class globally for all loggers including uvicorn
logging.setLoggerClass(AionLogger)

logger = get_logger()

dotenv_path = load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), verbose=True)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


async def async_serve(agent_id: str, agent_config: AgentConfig, startup_callback=None):
    try:
        aion_agent = await agent_manager.create_agent(
            agent_id=agent_id,
            config=agent_config)

        app_factory = await AppFactory(
            aion_agent=aion_agent,
            context=AppContext(
                db_manager=db_manager,
                store_manager=store_manager
            ),
            startup_callback=startup_callback
        ).initialize()

        if not app_factory:
            return

        uconfig = uvicorn.Config(
            app=app_factory.get_starlette_app(),
            host=aion_agent.host,
            port=aion_agent.port,
            log_config=None,
            access_log=False
        )
        server = uvicorn.Server(config=uconfig)

        await server.serve()

    except MissingAPIKeyError as ex:
        logger.error(f'Error: {ex}')
        exit(1)

    except Exception as ex:
        logger.error(f'An error occurred during server startup: {ex}')
        exit(1)


async def run_server(agent_id: str, agent_config: AgentConfig, startup_callback=None):
    """Starts the Currency Agent server."""
    try:
        # Configure custom loggers from uvicorn / logstash
        replace_uvicorn_loggers(suppress_startup_logs=True)
        replace_logstash_loggers()

        # Register agent adapters to handle a specific agent framework
        register_available_adapters()

        # Run agent server
        await async_serve(agent_id, agent_config, startup_callback)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)

    except Exception as ex:
        logger.error(f"Fatal error: {ex}")
        sys.exit(1)
