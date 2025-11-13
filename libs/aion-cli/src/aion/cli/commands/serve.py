"""CLI command for serving AION agents and proxy"""
from dataclasses import dataclass

import asyncclick as click
from aion.cli.handlers import ServeHandler
from aion.shared.config.reader import ConfigurationError, AionConfigReader
from aion.shared.logging import get_logger

logger = get_logger()


@dataclass
class PortAllocationStrategy:
    """Strategy for allocating ports to proxy and agents."""

    proxy_port: int | None
    port_range_start: int
    port_range_end: int
    proxy_port_search_start: int
    proxy_port_search_end: int

    @classmethod
    def calculate(
        cls,
        proxy_port: int | None,
        port_range_start: int | None,
        port_range_end: int | None
    ) -> "PortAllocationStrategy":
        """
        Calculate port allocation strategy based on CLI parameters.

        Args:
            proxy_port: Explicit proxy port (None = auto-find)
            port_range_start: Starting port of range (None = auto-calculate)
            port_range_end: Ending port of range (None = auto-calculate)

        Returns:
            PortAllocationStrategy with calculated values
        """
        # Auto-calculate port range start if not specified
        if port_range_start is None:
            # If proxy port explicitly specified, start range after it
            # Otherwise use default 8000
            port_range_start = (proxy_port + 1) if proxy_port is not None else 8000

        # Auto-calculate port range end if not specified
        if port_range_end is None:
            # Default range: 1000 ports
            port_range_end = port_range_start + 1000

        # Determine proxy port search range
        if proxy_port is None:
            # Use port range for proxy search
            proxy_port_search_start = port_range_start
            proxy_port_search_end = port_range_end
        else:
            # Proxy port explicitly specified, no search needed
            # These values won't be used, but set for consistency
            proxy_port_search_start = 8000
            proxy_port_search_end = 8100

        return cls(
            proxy_port=proxy_port,
            port_range_start=port_range_start,
            port_range_end=port_range_end,
            proxy_port_search_start=proxy_port_search_start,
            proxy_port_search_end=proxy_port_search_end
        )


@click.command(name="serve")
@click.option(
    "--port",
    type=int,
    default=None,
    help="Port for the proxy server (if not specified, will auto-find starting from 8000)"
)
@click.option(
    "--port-range-start",
    type=int,
    default=None,
    help="Starting port of the range for proxy and agents (default: proxy_port + 1 if proxy specified, else 8000)"
)
@click.option(
    "--port-range-end",
    type=int,
    default=None,
    help="Ending port of the range for proxy and agents (default: port_range_start + 1000)"
)
async def serve(
    port: int | None,
    port_range_start: int | None,
    port_range_end: int | None
) -> None:
    """Run all configured AION agents and proxy server in separate processes"""

    try:
        # Load configuration
        reader = AionConfigReader()
        config = reader.load_and_validate_config()

        if not config.agents:
            raise ConfigurationError(
                message="No agents configured, please add agents to your AION configuration"
            )

        # Calculate port allocation strategy
        strategy = PortAllocationStrategy.calculate(
            proxy_port=port,
            port_range_start=port_range_start,
            port_range_end=port_range_end
        )

        # Run complete lifecycle through handler
        handler = ServeHandler()
        await handler.run(
            config=config,
            proxy_port=strategy.proxy_port,
            port_range_start=strategy.port_range_start,
            port_range_end=strategy.port_range_end,
            proxy_port_search_start=strategy.proxy_port_search_start,
            proxy_port_search_end=strategy.proxy_port_search_end
        )

    except Exception as ex:
        logger.exception(f"Failed to start server: {str(ex)}")
        raise click.ClickException(f"Unable to start AION system: {str(ex)}")
