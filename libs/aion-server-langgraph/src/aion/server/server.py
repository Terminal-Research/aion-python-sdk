import logging
import os
import sys

import uvicorn
from aion.shared.aion_config import AgentConfig
from aion.shared.logging import get_logger
from aion.shared.logging.base import AionLogger
from aion.shared.utils import replace_uvicorn_loggers, replace_logstash_loggers
from dotenv import load_dotenv

from aion.server.adapters import register_available_adapters
from aion.server.core.agent import agent_manager
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
        agent_manager.set_agent_config(agent_id, agent_config)
        aion_agent = await agent_manager.create_agent()
        d1=1

        app_factory = await AppFactory(
            agent_id=agent_id,
            agent_config=agent_config,
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
            host=app_factory.get_agent_host(),
            port=app_factory.get_agent_config().port,
            log_config=None,
            access_log=False
        )
        server = uvicorn.Server(config=uconfig)

        await server.serve()

    except MissingAPIKeyError as e:
        logger.error(f'Error: {e}')
        exit(1)

    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
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

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
