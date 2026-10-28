"""Entry points for running the Aion agent server process."""

import aion.core.logging.base  # noqa: F401
import functools
import logging
import socket
import sys
import uvicorn
from collections.abc import Awaitable, Callable
from typing import Optional
from aion.core.config import AgentConfig
from aion.core.exceptions import AionError
from aion.db.postgres import DbFactory, db_manager
from aion.server.agent import AgentFactory
from aion.server.agent import agent_manager
from aion.server.core.app import AppFactory
from aion.server.logging import setup_root_logger
from aion.server.plugins import PluginFactory
from aion.server.shutdown import MIN_SANE_SHUTDOWN_SECONDS, shutdown_budget
from aion.server.opentelemetry.startup import StartupSpan
from aion.server.startup import StartupServer
from aion.server.tasks import store_manager

logger = logging.getLogger(__name__)


class MissingAPIKeyError(AionError):
    """Exception for missing API key."""


class _AgentServer(StartupServer):
    """uvicorn's server, which stops the running turns as soon as it stops accepting requests.

    uvicorn's shutdown stops listening, asks every connection to close once
    its response is complete, waits for open connections up to
    ``timeout_graceful_shutdown`` and only then runs the application's
    shutdown. A stream on a task whose turn stops closes with the turn; a
    stream on any other task would hold the shutdown for as long as its turn
    runs, so after that wait uvicorn cancels the requests still open and the
    drain of the active tasks settles what they were following. The turns
    have to be asked to stop before that wait, so that the stream open on
    their task closes with its outcome rather than holding the wait;
    ``before_drain`` runs right after the server stops accepting.
    """

    def __init__(
        self, config: uvicorn.Config, before_drain: Callable[[], Awaitable[None]],
        startup_span: StartupSpan | None = None, startup_callback=None,
    ) -> None:
        super().__init__(
            config, startup_span or StartupSpan("agent.startup"), "Agent", logger,
            startup_callback,
        )
        self._before_drain = before_drain

    async def shutdown(self, sockets: Optional[list[socket.socket]] = None) -> None:
        for server in self.servers:
            server.close()
        for sock in sockets or []:
            sock.close()
        for connection in list(self.server_state.connections):
            connection.shutdown()
        if not self.force_exit:
            await self._before_drain()
        await super().shutdown(sockets=sockets)


async def async_serve(
        agent_id: str,
        agent_config: AgentConfig,
        port: int,
        startup_callback=None,
        serialized_socket=None,
        startup_span: StartupSpan | None = None,
):
    """Initialize the agent, build the FastAPI app, and run uvicorn until shutdown."""
    startup = startup_span or StartupSpan("agent.startup")
    try:
        with startup.activate():
            aion_agent = await agent_manager.create_agent(
                agent_id=agent_id,
                port=port,
                config=agent_config)

            # Create factories via dependency injection
            db_factory = DbFactory(db_manager=db_manager)
            agent_factory = AgentFactory(aion_agent=aion_agent)
            plugin_factory = PluginFactory(db_manager=db_manager)

            # Create and initialize AppFactory with all dependencies
            app_factory = await AppFactory(
                aion_agent=aion_agent,
                db_factory=db_factory,
                agent_factory=agent_factory,
                plugin_factory=plugin_factory,
                store_manager=store_manager,
                startup_context=startup.context
            ).initialize()

            if not app_factory:
                startup.finish("failed")
                return

            budget = shutdown_budget()
            if budget.too_short:
                logger.warning(
                    "AION_SHUTDOWN_TIMEOUT=%gs is below %gs: a running turn gets %.1fs to stop and save its work, "
                    "uvicorn %.1fs for open connections, and the settlement of interrupted tasks %.1fs",
                    budget.total,
                    MIN_SANE_SHUTDOWN_SECONDS,
                    budget.turns,
                    budget.graceful,
                    budget.settlement,
                )

            # Deserialize socket if provided
            sockets = None
            if serialized_socket is not None:
                from aion.server.utils.ports.reservation import deserialize_socket
                sock = deserialize_socket(serialized_socket)
                sockets = [sock]
                logger.debug(f"Using passed socket for agent '{agent_id}' on port {port}")

            uconfig = uvicorn.Config(
                app=app_factory.get_fastapi_app(),
                host=aion_agent.host if sockets is None else None,
                port=aion_agent.port if sockets is None else None,
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=budget.graceful,
            )
            # If we have sockets, we need to manually set them on the server
            server = _AgentServer(
                config=uconfig,
                before_drain=functools.partial(app_factory.stop_turns, budget.turns),
                startup_span=startup,
                startup_callback=startup_callback,
            )
            if sockets is not None:
                server.servers = []  # Clear default servers

        await server.serve(sockets=sockets)

    except MissingAPIKeyError as ex:
        startup.finish('failed', ex)
        exit(1)

    except Exception as ex:
        if not startup.finished:
            startup.finish('failed', ex)
        else:
            logger.error(f'An error occurred while serving: {ex}')
        exit(1)


async def run_server(
        agent_id: str,
        agent_config: AgentConfig,
        port: int,
        startup_callback=None,
        serialized_socket=None,
        startup_span: StartupSpan | None = None,
):
    """Configure logging, then run the agent server until completion or interrupt."""
    startup = startup_span or StartupSpan("agent.startup")
    try:
        from aion.core.logging import set_process_role

        # Covers the lines this process writes before the agent is loaded and can
        # name itself; once it is, the agent's own name takes over.
        set_process_role("Agent")
        setup_root_logger()
        with startup.activate():
            logger.info("Agent server starting: agent_id=%s port=%s", agent_id, port)

        # RUN AGENT
        await async_serve(agent_id, agent_config, port, startup_callback, serialized_socket, startup)
        logger.info("Agent server stopped: agent_id=%s", agent_id)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)

    except Exception as ex:
        logger.error(f"Fatal error: {ex}")
        sys.exit(1)
    finally:
        if not startup.finished:
            startup.finish("failed")
