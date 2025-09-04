import logging
import os
import sys

import uvicorn
from dotenv import load_dotenv

from aion.server.core.app import AppFactory, AppConfig

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


async def main(host: str = "localhost", port: int = 10000):
    """Starts the Currency Agent server."""
    try:
        await async_serve(host, port)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
