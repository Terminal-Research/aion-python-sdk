import asyncio
import logging

import pytest

from aion.server.core.platform import AionWebSocketManager
from aion.server.core.platform.websocket import ws_manager as ws_manager_module


async def wait_for(predicate, timeout=2.0):
    """Poll until predicate holds, so tests never depend on a fixed sleep."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


class TestAionWebSocketManager:
    """Unit tests for AionWebSocketManager."""

    def test_init_sets_dependencies(self, transport_factory):
        """Test initialization properly sets dependencies and default values."""
        manager = AionWebSocketManager(
            ws_transport_factory=transport_factory,
            reconnect_delay=2.0,
            max_reconnect_delay=15.0,
        )

        assert manager._ws_transport_factory == transport_factory
        assert manager.reconnect_delay == 2.0
        assert manager.max_reconnect_delay == 15.0
        assert manager._websocket_task is None
        assert not manager._shutdown_event.is_set()
        assert not manager._connected.is_set()
        assert manager._transport is None

    def test_is_connected_is_false_before_start(self, ws_manager):
        """Asserted by identity: the interface promises a bool, not a falsy None."""
        assert ws_manager.is_connected is False

    async def test_start_connects_and_reports_connected(self, ws_manager, transport_factory):
        """A successful start leaves a live transport and an honest is_connected."""
        await ws_manager.start()

        assert ws_manager.is_connected is True
        assert len(transport_factory.created) == 1
        assert transport_factory.created[0].connect_calls == 1

        await ws_manager.stop()

    async def test_start_is_idempotent(self, started_ws_manager, transport_factory):
        """Starting twice must not leave a second loop dialing in the background."""
        await started_ws_manager.start()

        assert len(transport_factory.created) == 1

    async def test_reconnects_after_the_connection_drops(self, ws_manager, transport_factory):
        """The whole point: a dropped connection comes back without a restart."""
        await ws_manager.start()

        transport_factory.created[0].drop(ConnectionResetError("peer went away"))

        assert await wait_for(lambda: len(transport_factory.created) == 2)
        assert await wait_for(lambda: ws_manager.is_connected)
        assert ws_manager._reconnects == 1

        await ws_manager.stop()

    async def test_is_connected_goes_false_while_the_link_is_down(
        self, ws_manager, transport_factory
    ):
        """is_connected used to lie in both directions across an outage.

        The old loop cleared the flag only after a failed redial and never set it
        again, so it reported a healthy link during an outage and a dead one long
        after the connection had come back.
        """
        await ws_manager.start()
        # A backoff long enough to observe the gap rather than race it.
        ws_manager.reconnect_delay = 30.0
        ws_manager.max_reconnect_delay = 30.0

        transport_factory.created[0].drop(OSError("dropped"))

        assert await wait_for(lambda: ws_manager.is_connected is False)

        await ws_manager.stop()

    async def test_logs_why_the_connection_was_lost(self, ws_manager, transport_factory, caplog):
        """The close reason is the diagnostic; without it outages are unreadable."""
        await ws_manager.start()

        with caplog.at_level(logging.WARNING):
            transport_factory.created[0].drop(
                ConnectionResetError("sent 1011 (internal error) keepalive ping timeout"))
            assert await wait_for(lambda: len(transport_factory.created) == 2)

        lost = [r.getMessage() for r in caplog.records if "lost" in r.getMessage()]
        assert lost, "a lost connection must say so at warning level"
        assert "keepalive ping timeout" in lost[0]

        await ws_manager.stop()

    async def test_a_lost_connection_reports_how_long_it_lasted(
        self, ws_manager, transport_factory, caplog
    ):
        """A socket killed mid-flight carries no close code and no reason.

        Its lifetime is then the only thing left to reason from - lifetimes that
        cluster around one value mean something reaps us on a timer, scattered
        ones mean the peer went away - and debug is off in production, so the
        liveness lines are not there to count.
        """
        await ws_manager.start()

        with caplog.at_level(logging.WARNING):
            transport_factory.created[0].drop(
                ConnectionResetError("no close frame received or sent"))
            assert await wait_for(lambda: len(transport_factory.created) == 2)

        lost = [r.getMessage() for r in caplog.records if "lost" in r.getMessage()]
        assert "lost after" in lost[0], f"no connection age in {lost[0]!r}"

        await ws_manager.stop()

    @pytest.mark.parametrize("seconds, expected", [
        (0.4, "0s"),
        (119, "119s"),
        # Past two minutes a bare second count stops being readable at a glance,
        # which is the only reason anyone looks at this field.
        (120, "2m00s"),
        (932, "15m32s"),
        (3600, "60m00s"),
    ])
    def test_durations_stay_readable_at_any_length(self, seconds, expected):
        assert ws_manager_module._format_duration(seconds) == expected

    async def test_connection_log_names_the_endpoint_without_the_token(
        self, ws_manager, caplog
    ):
        """Operators need to see where we connected; they must not see the token.

        The transport's URL carries the auth token in its query string, which is
        why it is trimmed rather than logged whole.
        """
        with caplog.at_level(logging.INFO):
            await ws_manager.start()

        established = [r.getMessage() for r in caplog.records if "established" in r.getMessage()]
        assert established, "a new connection must announce itself"
        assert "wss://api-staging.aion.to/ws/graphql" in established[0]
        assert "super-secret-jwt" not in established[0]

        await ws_manager.stop()

    async def test_a_healthy_link_reports_one_line_per_check(
        self, ws_manager, transport_factory, caplog, monkeypatch
    ):
        """The liveness check stands in for the frame-by-frame keepalive tracing.

        Silencing the transport's ping/pong would otherwise leave a healthy
        connection with nothing at all to show at debug level.
        """
        monkeypatch.setattr(ws_manager_module, "LIVENESS_CHECK_INTERVAL", 0.02)

        with caplog.at_level(logging.DEBUG, logger="aion.server.core.platform.websocket.ws_manager"):
            await ws_manager.start()
            assert await wait_for(
                lambda: sum("alive" in r.getMessage() for r in caplog.records) >= 2)

        await ws_manager.stop()

    async def test_retries_until_the_platform_comes_back(self, ws_manager, transport_factory, make_transport):
        """Repeated failures must not end the loop; only shutdown does."""
        transport_factory.transports = [
            make_transport(connect_error=OSError("refused")),
            make_transport(connect_error=OSError("refused")),
            make_transport(),
        ]

        with pytest.raises(ConnectionError):
            await ws_manager.start()

        assert await wait_for(lambda: ws_manager.is_connected)
        assert len(transport_factory.created) == 3

        await ws_manager.stop()

    async def test_start_raises_but_keeps_dialing(self, ws_manager, transport_factory, make_transport):
        """A platform briefly unreachable at boot must not need an agent restart."""
        transport_factory.transports = [make_transport(connect_error=OSError("refused"))]

        with pytest.raises(ConnectionError):
            await ws_manager.start()

        assert ws_manager._websocket_task is not None
        assert not ws_manager._websocket_task.done()

        await ws_manager.stop()

    async def test_a_failed_dial_closes_its_transport(self, ws_manager, transport_factory, make_transport):
        """Half-open transports from failed dials must not pile up."""
        failed = make_transport(connect_error=OSError("refused"))
        transport_factory.transports = [failed]

        with pytest.raises(ConnectionError):
            await ws_manager.start()

        assert failed.close_calls >= 1

        await ws_manager.stop()

    async def test_backoff_grows_with_consecutive_failures(self, ws_manager):
        """Delays climb toward the ceiling instead of hammering a downed platform."""
        ws_manager.reconnect_delay = 1.0
        ws_manager.max_reconnect_delay = 8.0
        delays = []

        async def record(timeout):
            delays.append(timeout)
            return False

        ws_manager._wait_for_shutdown = record

        for attempt in range(5):
            await ws_manager._sleep_before_retry(attempt)

        # Jitter keeps each delay within half of its nominal value, so assert the
        # band rather than the number.
        assert delays[0] <= 1.0
        assert delays[1] > delays[0] / 2
        assert all(delay <= 8.0 for delay in delays)
        assert delays[-1] >= 4.0

    async def test_backoff_survives_a_very_long_outage(self, ws_manager):
        """A days-long outage must not overflow the doubling into an exception."""
        delays = []

        async def record(timeout):
            delays.append(timeout)
            return False

        ws_manager._wait_for_shutdown = record

        await ws_manager._sleep_before_retry(5000)

        assert delays[0] <= ws_manager.max_reconnect_delay

    async def test_a_connection_that_vanishes_is_still_noticed(
        self, ws_manager, transport_factory, monkeypatch
    ):
        """Backstop for a transport that drops its socket without reporting a close."""
        monkeypatch.setattr(ws_manager_module, "LIVENESS_CHECK_INTERVAL", 0.02)

        await ws_manager.start()
        # Sever the socket without ever setting the closed event.
        transport_factory.created[0].websocket = None

        assert await wait_for(lambda: len(transport_factory.created) == 2)

        await ws_manager.stop()

    async def test_stop_is_prompt_and_quiet(self, ws_manager, caplog):
        """Shutdown must not sit through a wait or end in a forced cancellation."""
        await ws_manager.start()

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(ws_manager.stop(), timeout=1.0)

        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []

    async def test_stop_closes_the_transport(self, ws_manager, transport_factory):
        """The transport is closed on the way out, not merely abandoned."""
        await ws_manager.start()
        transport = transport_factory.created[0]

        await asyncio.wait_for(ws_manager.stop(), timeout=1.0)

        assert transport.close_calls == 1
        assert ws_manager.is_connected is False

    async def test_stop_during_backoff_does_not_wait_it_out(self, ws_manager, transport_factory, make_transport):
        """Shutdown interrupts the backoff sleep instead of sitting through it."""
        ws_manager.reconnect_delay = 30.0
        ws_manager.max_reconnect_delay = 30.0
        transport_factory.transports = [make_transport(connect_error=OSError("refused"))]

        with pytest.raises(ConnectionError):
            await ws_manager.start()

        await asyncio.wait_for(ws_manager.stop(), timeout=1.0)
        assert ws_manager.is_connected is False

    async def test_stop_without_start_is_a_no_op(self, ws_manager):
        """Shutdown of an agent whose connection never started must not raise."""
        await ws_manager.stop()

    async def test_connection_state_summarizes_the_link(self, ws_manager, transport_factory):
        """Health reporting reads this, so it has to track reality."""
        await ws_manager.start()
        assert ws_manager.connection_state == {
            "status": "connected", "reconnects": 0, "lastError": None}

        transport_factory.created[0].drop(OSError("boom"))
        assert await wait_for(lambda: ws_manager._reconnects == 1)

        state = ws_manager.connection_state
        assert state["reconnects"] == 1
        assert "boom" in state["lastError"]

        await ws_manager.stop()

    async def test_a_crashing_loop_is_restarted(self, ws_manager):
        """A bug in the loop must not leave the agent silently offline."""
        calls = []
        real_loop = ws_manager._connection_loop

        async def crash_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("unexpected")
            await real_loop()

        ws_manager._connection_loop = crash_once
        ws_manager.max_reconnect_delay = 0.01

        with pytest.raises(ConnectionError):
            await ws_manager.start()

        assert await wait_for(lambda: len(calls) >= 2)
        assert await wait_for(lambda: ws_manager.is_connected)

        await ws_manager.stop()
