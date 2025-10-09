"""CLI command for serving AION agents and proxy"""

import asyncclick as click
from aion.shared.aion_config.reader import ConfigurationError, AionConfigReader
from aion.shared.logging import get_logger

from aion.cli.handlers import ServerManager
from aion.cli.utils.cli_messages import welcome_message

logger = get_logger()


@click.command(name="serve")
async def serve() -> None:
    """Run all configured AION agents and proxy server in separate processes"""

    server_manager = ServerManager()

    try:
        # Load configuration
        reader = AionConfigReader()
        config = reader.load_and_validate_config()

        if not config.agents:
            raise ConfigurationError(
                message="No agents configured, please add agents to your AION configuration"
            )

        use_proxy = bool(config.proxy)

        # Initialize server manager
        server_manager.initialize()

        # Start all configured agents
        successful_agents, failed_agents = server_manager.start_all_agents(config)

        # Report agent startup results
        if successful_agents:
            logger.info(f"Successfully started agents: {', '.join(successful_agents)}")

        if failed_agents:
            logger.error(f"Failed to start agents: {', '.join(failed_agents)}")

        if not successful_agents:
            logger.error("No agents started successfully, exiting...")
            return

        # Start proxy server if not disabled
        proxy_started = False
        if use_proxy:
            if server_manager.start_proxy(config):
                proxy_started = True
            else:
                logger.error("Failed to start proxy server")

        print(welcome_message(aion_config=config, proxy_enabled=proxy_started))

        # Monitor processes and keep main process running
        await server_manager.monitor_processes(successful_agents, proxy_started, config)

    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        raise click.ClickException(f"Unable to start AION system: {str(e)}")

    finally:
        # Ensure graceful shutdown
        server_manager.shutdown()
