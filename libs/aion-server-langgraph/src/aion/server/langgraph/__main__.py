import logging
import os

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
from .a2a.agent import CurrencyAgent
from .a2a.agent_executor import CurrencyAgentExecutor
from dotenv import load_dotenv


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Log the current working directory
cwd = os.getcwd()
logger.info(f"Current working directory: {cwd}")

import io
import logging
import os
import pathlib
import shutil
import sys
import tempfile
# Recreate logic from dotenv.find_dotenv to log the search process
frame = sys._getframe()
current_file = __file__
logger.info(f"Starting frame search from: {current_file}")

# Find the frame that's not from this file
while frame.f_code.co_filename == current_file or not os.path.exists(frame.f_code.co_filename):
    logger.info(f"Skipping frame: {frame.f_code.co_filename}")
    assert frame.f_back is not None
    frame = frame.f_back

# Get the directory of the calling file
frame_filename = frame.f_code.co_filename
logger.info(f"Found calling frame: {frame_filename}")
path = os.path.dirname(os.path.abspath(frame_filename))
logger.info(f"Starting directory search from: {path}")

# Simulate walking to root and checking each directory
def _walk_to_root(start_path):
    """Yield directories starting from the given directory up to the root"""
    if not os.path.exists(start_path):
        logger.warning(f"Start path does not exist: {start_path}")
        return
    
    if os.path.isfile(start_path):
        start_path = os.path.dirname(start_path)
        
    current_dir = os.path.abspath(start_path)
    logger.info(f"Walking directory tree from: {current_dir}")
    
    while True:
        yield current_dir
        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            break
        current_dir = parent_dir

# Log each directory that would be checked
env_file = '.env'
for dirname in _walk_to_root(path):
    check_path = os.path.join(dirname, env_file)
    logger.info(f"Checking for .env file at: {check_path}")
    if os.path.isfile(check_path):
        logger.info(f"Found .env file at: {check_path}")
        break

# Load environment variables with verbose output
dotenv_path = load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), verbose=True)

# Log result of load_dotenv
if dotenv_path:
    logger.info(f"Loaded .env file from: {dotenv_path}")
else:
    logger.warning("No .env file was found or loaded")


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


@click.command()
@click.option('--host', 'host', default='localhost')
@click.option('--port', 'port', default=10000)
def main(host, port):
    """Starts the Currency Agent server."""
    try:
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
            defaultInputModes=CurrencyAgent.SUPPORTED_CONTENT_TYPES,
            defaultOutputModes=CurrencyAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        # --8<-- [start:DefaultRequestHandler]
        httpx_client = httpx.AsyncClient()
        request_handler = DefaultRequestHandler(
            agent_executor=CurrencyAgentExecutor(),
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
