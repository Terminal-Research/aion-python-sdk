"""CLI command for serving AION agents and proxy"""
import asyncio

import asyncclick as click
from aion.cli.handlers import ServeHandler
from aion.cli.services import AionConfigBroadcastService
from aion.cli.utils.cli_messages import welcome_message
from aion.shared.aion_config.reader import ConfigurationError, AionConfigReader
from aion.shared.logging import get_logger

logger = get_logger()


@click.command(name="serve")
async def serve() -> None:
    """Run all configured AION agents and proxy server in separate processes"""

    handler = ServeHandler()

    try:
        # Load configuration
        reader = AionConfigReader()
        config = reader.load_and_validate_config()

        if not config.agents:
            raise ConfigurationError(
                message="No agents configured, please add agents to your AION configuration"
            )

        # Start servers
        successful_agents, failed_agents, proxy_started = await handler.startup(config)

        # Exit if no agents started successfully
        if not successful_agents:
            return

        # Broadcast config to aion api
        asyncio.create_task(AionConfigBroadcastService().execute(config))

        # Print welcome message after successful startup
        print(welcome_message(aion_config=config, proxy_enabled=proxy_started))

        # Monitor processes (blocking call until shutdown)
        await handler.monitor()

    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        raise click.ClickException(f"Unable to start AION system: {str(e)}")

    finally:
        # Ensure graceful shutdown
        await handler.shutdown()
