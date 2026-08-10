"""Manager that maintains a persistent WebSocket connection to the Aion platform."""

import asyncio
import logging
import random
from typing import Any, Optional
from urllib.parse import urlsplit

from aion.server.interfaces import IWebsocketTransportFactory, IWebSocketManager

logger = logging.getLogger(__name__)

__all__ = [
    "AionWebSocketManager",
]

# The transport tells us when a connection dies: it already runs a websocket-level
# ping and, above it, the graphql-ws keepalive, and either one closes the transport
# on silence. This wakeup is only a backstop for a transport that dropped its socket
# without ever reporting the close, which would otherwise strand the loop for good.
LIVENESS_CHECK_INTERVAL = 60.0

# Keeps 2 ** attempt from overflowing float once an outage has lasted for days.
MAX_BACKOFF_EXPONENT = 16


def _format_duration(seconds: float) -> str:
    """Render an elapsed time the way someone reading an outage would say it."""
    if seconds < 120:
        return f"{seconds:.0f}s"
    return f"{int(seconds) // 60}m{int(seconds) % 60:02d}s"


class AionWebSocketManager(IWebSocketManager):
    """
    WebSocket connection manager for Aion platform with dependency injection.

    Keeps a single connection to the platform open for the lifetime of the agent,
    redialing with exponential backoff whenever it drops. Every attempt builds a
    fresh transport, so each one carries a freshly minted token rather than the
    one that happened to be valid when the agent booted.
    """

    def __init__(
            self,
            ws_transport_factory: IWebsocketTransportFactory,
            reconnect_delay: float = 1.0,
            max_reconnect_delay: float = 60.0,
            connect_timeout: float = 30.0,
            startup_timeout: float = 30.0,
            stop_timeout: float = 5.0,
    ):
        """
        Initialize WebSocket manager with injected dependencies.

        Args:
            ws_transport_factory: Factory for creating WebSocket transports
            reconnect_delay: Base delay before a redial (seconds); doubles per
                consecutive failure up to max_reconnect_delay
            max_reconnect_delay: Ceiling for the backoff delay (seconds)
            connect_timeout: Deadline for a single connection attempt (seconds)
            startup_timeout: How long start() waits for the first attempt (seconds)
            stop_timeout: How long stop() waits before cancelling the loop (seconds)
        """
        self._ws_transport_factory = ws_transport_factory
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.connect_timeout = connect_timeout
        self.startup_timeout = startup_timeout
        self.stop_timeout = stop_timeout

        self._websocket_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._connected = asyncio.Event()
        self._startup_settled = asyncio.Event()
        self._transport: Optional[Any] = None

        self._ever_connected = False
        self._reconnects = 0
        self._last_error: Optional[str] = None
        self._connected_at: Optional[float] = None

    async def start(self) -> None:
        """Start WebSocket connection with the platform.

        Returns once the first connection is up. If it cannot be established the
        call raises, but the loop keeps redialing in the background: a platform
        that is briefly unreachable at boot must not leave the agent offline until
        someone restarts it.

        Raises:
            ConnectionError: If the first connection attempt did not succeed.
        """
        if self._websocket_task is not None:
            return

        self._shutdown_event.clear()
        self._startup_settled.clear()
        self._websocket_task = asyncio.create_task(self._run())

        try:
            await asyncio.wait_for(
                self._startup_settled.wait(), timeout=self.startup_timeout)
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"Timed out after {self.startup_timeout:.0f}s connecting to the Aion "
                f"platform; still retrying in the background")

        if not self._connected.is_set():
            raise ConnectionError(
                f"Failed to connect to the Aion platform ({self._last_error}); "
                f"still retrying in the background")

    async def stop(self) -> None:
        """Stop WebSocket connection."""
        task, self._websocket_task = self._websocket_task, None
        if task is None:
            return

        logger.info("Stopping connection to Aion platform...")
        self._shutdown_event.set()

        if not task.done():
            try:
                await asyncio.wait_for(task, timeout=self.stop_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "Platform connection did not stop within %.0fs, cancelled it",
                    self.stop_timeout)
            except asyncio.CancelledError:
                # Tolerate a loop that something else had already cancelled, but
                # never swallow a cancellation aimed at whoever called stop().
                if not task.cancelled():
                    raise

        self._connected.clear()
        logger.info("Connection to Aion platform closed")

    async def _run(self) -> None:
        """Own the connection loop, restarting it if it ever fails outright.

        The loop below is written not to raise, so reaching the handler means a bug
        rather than an outage. Restarting beats propagating into a task nobody
        awaits, where the agent would go quietly offline until it was restarted.
        """
        try:
            while not self._shutdown_event.is_set():
                try:
                    await self._connection_loop()
                    return
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    logger.exception("Platform connection loop crashed, restarting it")
                    self._startup_settled.set()
                    self._connected.clear()
                    await self._close_transport()
                    if await self._wait_for_shutdown(self.max_reconnect_delay):
                        return
        finally:
            self._connected.clear()
            await self._close_transport()

    async def _connection_loop(self) -> None:
        """Keep a connection to the platform up until shutdown is requested."""
        attempt = 0
        first_dial = True

        while not self._shutdown_event.is_set():
            if not first_dial and await self._sleep_before_retry(attempt):
                return
            first_dial = False

            try:
                await self._open_transport()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                attempt += 1
                self._last_error = f"{type(ex).__name__}: {ex}"
                logger.warning(
                    "Could not connect to Aion platform (attempt %d): %s",
                    attempt, self._last_error)
                self._startup_settled.set()
                await self._close_transport()
                continue

            attempt = 0
            self._on_connected()

            reason = await self._wait_until_closed()
            self._connected.clear()
            await self._close_transport()

            if self._shutdown_event.is_set():
                return

            self._last_error = reason
            # How long the connection lasted is the diagnostic the close reason
            # cannot give: a socket killed mid-flight reports no code and no
            # reason at all, and lifetimes that cluster around one value say
            # "something reaps us on a timer" where a single line says nothing.
            # Debug is off in production, so the liveness lines are not there to
            # count either.
            logger.warning(
                "Connection to Aion platform lost after %s (%s), reconnecting",
                _format_duration(self._uptime()), reason)

    def _on_connected(self) -> None:
        """Publish a successful connection and log it the way operators need."""
        self._connected.set()
        self._connected_at = asyncio.get_running_loop().time()
        endpoint = self._describe_endpoint(self._transport)

        if self._ever_connected:
            self._reconnects += 1
            # Only the very first connection used to be logged, which is how a
            # connection that drops and comes back reads as one that stayed down
            # until someone restarted the agent.
            logger.info(
                "Reconnected to Aion platform at %s (reconnect #%d)",
                endpoint, self._reconnects)
        else:
            logger.info("Connection to Aion platform established (%s)", endpoint)

        self._ever_connected = True
        self._startup_settled.set()

    async def _open_transport(self) -> None:
        """Build a fresh transport and connect it, within connect_timeout."""
        async def dial() -> None:
            self._transport = await self._ws_transport_factory.create_transport()
            await self._transport.connect()

        await asyncio.wait_for(dial(), timeout=self.connect_timeout)

    async def _wait_until_closed(self) -> str:
        """Block until the live connection dies, describing why it did.

        Waking periodically covers the one case the transport cannot report: a
        socket dropped without the close ever being signalled.
        """
        transport = self._transport
        shutdown = asyncio.ensure_future(self._shutdown_event.wait())
        closed = asyncio.ensure_future(transport.wait_closed())

        try:
            while True:
                done, _ = await asyncio.wait(
                    {shutdown, closed},
                    timeout=LIVENESS_CHECK_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if shutdown in done:
                    return "shutdown requested"
                if closed in done:
                    return self._describe_close(transport)
                if getattr(transport, "websocket", None) is None:
                    return "transport dropped its socket without reporting a close"

                # One line per check stands in for the keepalive ping/pong the
                # transport traces frame by frame, which says the same thing at
                # roughly fifteen times the volume.
                logger.debug("Platform connection alive (up %.0fs)", self._uptime())
        finally:
            for pending in (shutdown, closed):
                pending.cancel()

    def _uptime(self) -> float:
        """Seconds the current connection has been up, 0 if there is none."""
        if self._connected_at is None:
            return 0.0
        return asyncio.get_running_loop().time() - self._connected_at

    @staticmethod
    def _describe_endpoint(transport: Any) -> str:
        """Name the endpoint we are talking to, minus the credentials.

        The transport carries its auth token in the URL's query string, so the
        URL can never be logged whole.
        """
        url = getattr(transport, "url", None)
        if not url:
            return "unknown endpoint"

        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.netloc else url

    @staticmethod
    def _describe_close(transport: Any) -> str:
        """Render the transport's own account of why it closed.

        gql keeps the exception that closed the connection, which is where the
        websocket close code and the peer's reason end up.
        """
        error = getattr(transport, "close_exception", None)
        return f"{type(error).__name__}: {error}" if error else "closed by peer"

    async def _close_transport(self) -> None:
        """Release the current transport, tolerating one that is already gone."""
        transport, self._transport = self._transport, None
        if transport is None:
            return

        try:
            await asyncio.wait_for(transport.close(), timeout=self.stop_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.debug("Ignoring error while closing platform transport: %s", ex)

    async def _sleep_before_retry(self, attempt: int) -> bool:
        """Wait out the backoff, returning True if shutdown arrived first.

        Jittered so that agents knocked offline together by one platform restart
        do not all redial on the same tick.
        """
        delay = min(
            self.reconnect_delay * (2 ** min(attempt, MAX_BACKOFF_EXPONENT)),
            self.max_reconnect_delay,
        )
        delay *= 0.5 + random.random() / 2

        logger.info("Reconnecting to Aion platform in %.1fs", delay)
        return await self._wait_for_shutdown(delay)

    async def _wait_for_shutdown(self, timeout: float) -> bool:
        """Wait up to timeout for shutdown, returning True if it was requested."""
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def is_connected(self) -> bool:
        """
        Check if connection to the platform is active.

        Returns:
            True if connection is active, False otherwise
        """
        return bool(self._connected.is_set() and not self._shutdown_event.is_set())

    @property
    def connection_state(self) -> dict:
        """Summarize the platform connection for health reporting."""
        return {
            "status": "connected" if self.is_connected else "disconnected",
            "reconnects": self._reconnects,
            "lastError": self._last_error,
        }
