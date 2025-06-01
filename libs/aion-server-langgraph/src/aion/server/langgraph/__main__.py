import logging
import os
from typing import Optional

import click
import httpx
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryPushNotifier, InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from .a2a.agent import LanggraphAgent
from .a2a.agent_executor import LanggraphAgentExecutor
from .graph import GRAPHS, get_graph, initialize_graphs
from aion.server.db import get_config, test_connection
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

dotenv_path = load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), verbose=True)

class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


@click.command()
@click.option('--host', 'host', default='localhost')
@click.option('--port', 'port', default=10000)
def main(host, port):
    """Starts the Currency Agent server."""
    try:
        cfg = get_config()
        if cfg:
            test_connection(cfg.url)
        else:
            logger.info("POSTGRES_URL environment variable not set, using in-memory data store")
        initialize_graphs()
        if not GRAPHS:
            logger.error("No graphs found in configuration; shutting down")
            raise SystemExit(1)
        graph_id, graph_obj = next(iter(GRAPHS.items()))
        logger.info("Using graph '%s'", graph_id)
        if not os.getenv('OPENROUTER_API_KEY'):
            raise MissingAPIKeyError(
                'OPENROUTER_API_KEY environment variable not set.'
            )

        capabilities = AgentCapabilities(streaming=True, pushNotifications=True)
        skill = AgentSkill(
            id='convert_currency',
            name='Currency Exchange Rates Tool',
            description='Helps with exchange values between various currencies',
            tags=['currency conversion', 'currency exchange'],
            examples=['What is exchange rate between USD and GBP?'],
        )
        agent_card = AgentCard(
            name='Currency Agent',
            description='Helps with exchange rates for currencies',
            url=f'http://{host}:{port}/',
            version='1.0.0',
            defaultInputModes=LanggraphAgent.SUPPORTED_CONTENT_TYPES,
            defaultOutputModes=LanggraphAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        # --8<-- [start:DefaultRequestHandler]
        httpx_client = httpx.AsyncClient()
        request_handler = DefaultRequestHandler(
            agent_executor=LanggraphAgentExecutor(graph_obj),
            task_store=InMemoryTaskStore(),
            push_notifier=InMemoryPushNotifier(httpx_client),
        )
        server = A2AStarletteApplication(
            agent_card=agent_card, http_handler=request_handler
        )

        uvicorn.run(server.build(), host=host, port=port)
        # --8<-- [end:DefaultRequestHandler]

    except MissingAPIKeyError as e:
        logger.error(f'Error: {e}')
        exit(1)
    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        exit(1)


if __name__ == '__main__':
    main()
