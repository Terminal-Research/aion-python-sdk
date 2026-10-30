"""CLI command for serving AION agents and proxy"""
import logging
import asyncclick as click
from aion.core.config.reader import ConfigurationError, AionConfigReader
from aion.core.utils.optional_deps import (
    MissingOptionalDependency,
    is_own_module,
    missing_server_extra_error,
)
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
@click.option(
    "--startup-timeout",
    type=int,
    default=30,
    help="Timeout in seconds to wait for startup confirmation from agents and proxy (0 to skip, default: 30)"
)
async def serve(
        port: int | None,
        port_range_start: int | None,
        port_range_end: int | None,
        startup_timeout: int
) -> None:
    """Run all configured AION agents and proxy server in separate processes"""
    startup = None
    # Imported here rather than at module level: `aion serve` is the one
    # command that needs the server, and `aion --help` has to work in a base
    # install - it is where the hint below gets read.
    try:
        from aion.core.logging import set_process_role
        from aion.server.logging import setup_root_logger

        set_process_role("CLI")
        setup_root_logger()
        from aion.server.opentelemetry.startup import StartupSpan
        startup = StartupSpan("application.startup")

        with startup.activate():
            # Application imports validate database settings. Keep them inside the
            # logged failure path, after the logging handlers are ready.
            from aion.cli.handlers.serve import ServeHandler, ServeOutcome

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
        outcome = await handler.run(
            config=config,
            proxy_port=strategy.proxy_port,
            port_range_start=strategy.port_range_start,
            port_range_end=strategy.port_range_end,
            proxy_port_search_start=strategy.proxy_port_search_start,
            proxy_port_search_end=strategy.proxy_port_search_end,
            startup_timeout=startup_timeout,
            startup_span=startup,
        )

    except MissingOptionalDependency as ex:
        if startup is not None:
            startup.finish("failed", ex)
        raise click.ClickException(
            str(missing_server_extra_error("aion serve", ex))
        ) from ex
    except ModuleNotFoundError as ex:
        if startup is not None:
            startup.finish("failed", ex)
        if is_own_module(ex.name):
            raise
        raise click.ClickException(
            str(missing_server_extra_error("aion serve", ex))
        ) from ex
    except Exception as ex:
        if startup is not None and startup.outcome not in (None, "failed"):
            # The deployment started and this error stopped it while serving,
            # so it is reported outside the startup trace.
            logger.exception(f"AION system stopped on an error: {str(ex)}")
            raise click.ClickException(f"AION system stopped on an error: {str(ex)}")
        if startup is None:
            logger.exception(f"Failed to start server: {str(ex)}")
        elif not startup.finished:
            with startup.activate():
                logger.error(f"Failed to start server: {str(ex)}")
            # Logs the traceback once, with the failed outcome.
            startup.finish("failed", ex)
        # A startup the handler finished as failed has already logged its cause.
        raise click.ClickException(f"Unable to start AION system: {str(ex)}")
    except BaseException as ex:
        # An interrupt or a cancellation still ends the attempt it interrupted.
        if startup is not None and not startup.finished:
            startup.finish("failed", ex)
        raise

    # A deployment none of whose agents started has failed, whatever stopped
    # them - a wrong DEPLOYMENT_ID among other settings - and a supervisor has
    # to see that in the exit status, not a clean exit.
    if outcome is ServeOutcome.NOT_STARTED:
        raise click.ClickException("No agent started; the errors above say why")
    # Agents that exit by themselves end a deployment that was serving. The
    # startup keeps its outcome; the exit status reports the failure.
    if outcome is ServeOutcome.AGENTS_STOPPED:
        raise click.ClickException("All agents stopped while serving; the errors above say why")
