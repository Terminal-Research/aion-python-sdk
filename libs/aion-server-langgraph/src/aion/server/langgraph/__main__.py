import asyncio
import logging
import os
import sys

import click
import uvicorn
from aion.server.app import AppFactory, AppConfig
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

dotenv_path = load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), verbose=True)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


async def async_serve(host, port):
    try:
        if not os.getenv('OPENROUTER_API_KEY'):
            raise MissingAPIKeyError(
                'OPENROUTER_API_KEY environment variable not set.'
            )

        app_factory = await AppFactory.initialize(config=AppConfig(host=host, port=port))
        if not app_factory:
            return

        uconfig = uvicorn.Config(
            app=app_factory.starlette_app,
            host=app_factory.config.host,
            port=app_factory.config.port)
        server = uvicorn.Server(config=uconfig)

        await server.serve()

    except MissingAPIKeyError as e:
        logger.error(f'Error: {e}')
        exit(1)

    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        exit(1)


@click.command()
@click.option('--host', 'host', default='localhost')
@click.option('--port', 'port', default=10000)
def main(host, port):
    """Starts the Currency Agent server."""
    try:
        asyncio.run(async_serve(host, port))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
