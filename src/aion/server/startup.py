"""Uvicorn readiness reporting with bounded startup tracing."""

from __future__ import annotations

import logging
from collections.abc import Callable

import uvicorn
from opentelemetry.trace import StatusCode

from aion.server.opentelemetry.startup import StartupSpan, startup_phase, without_trace


class StartupServer(uvicorn.Server):
    """Report readiness after lifespan initialization and socket binding.

    Args:
        config: Uvicorn configuration.
        startup: The process's startup attempt.
        role: Human-readable process role for readiness logs.
        logger: Logger used by the owning server.
        startup_callback: Notify the launcher once the server is listening.
    """

    def __init__(
        self, config: uvicorn.Config, startup: StartupSpan, role: str,
        logger: logging.Logger, startup_callback: Callable[[], None] | None = None,
    ):
        super().__init__(config)
        self.startup_span = startup
        self._role = role
        self._logger = logger
        self._startup_callback = startup_callback

    async def startup(self, sockets=None) -> None:
        """Keep socket callbacks untraced; attribute readiness and failures."""
        try:
            with startup_phase("server.startup", self.startup_span.context) as span:
                # Socket callbacks capture context when registered. Neither
                # requests nor the long-lived lifespan task should inherit it.
                with without_trace():
                    await super().startup(sockets=sockets)
                if self.started:
                    self._logger.info("%s server ready; accepting requests", self._role)
                    if self._startup_callback is not None:
                        self._startup_callback()
                else:
                    self._logger.error(
                        "%s server startup failed; application did not become ready", self._role
                    )
                    span.set_status(StatusCode.ERROR)
            self.startup_span.finish("ready" if self.started else "failed")
        except BaseException as error:
            self.startup_span.finish("failed", error)
            raise
