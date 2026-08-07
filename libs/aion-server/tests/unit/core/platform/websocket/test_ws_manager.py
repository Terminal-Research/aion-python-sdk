import asyncio
import logging
from unittest.mock import MagicMock


from aion.server.core.platform import AionWebSocketManager


class TestAionWebSocketManager:
    """Unit tests for AionWebSocketManager."""

    def test_init_sets_dependencies(self, mock_ws_transport_factory):
        """Test initialization properly sets dependencies and default values."""
        manager = AionWebSocketManager(
            ws_transport_factory=mock_ws_transport_factory,
            ping_interval=25.0,
            reconnect_delay=15.0
        )

        assert manager._ws_transport_factory == mock_ws_transport_factory
        assert manager.ping_interval == 25.0
        assert manager.reconnect_delay == 15.0
        assert manager._websocket_task is None
        assert not manager._shutdown_event.is_set()
        assert not manager._connection_ready.is_set()
        assert manager._transport is None

    def test_is_connected_returns_false_when_no_task(self, ws_manager):
        """Test is_connected returns False when websocket_task is None.

        Asserted by identity: the chain used to return the untouched None from
        _websocket_task here, which is falsy but not the bool the interface promises.
        """
        assert ws_manager._websocket_task is None
        assert ws_manager.is_connected is False

    def test_is_connected_returns_false_when_task_done(self, ws_manager):
        """Test is_connected returns False when websocket task is completed."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = True
        ws_manager._connection_ready.set()

        assert ws_manager.is_connected is False

    def test_is_connected_returns_false_when_shutdown_set(self, ws_manager):
        """Test is_connected returns False when shutdown event is set."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = False
        ws_manager._connection_ready.set()
        ws_manager._shutdown_event.set()

        assert ws_manager.is_connected is False

    def test_is_connected_returns_false_when_not_ready(self, ws_manager):
        """Test is_connected returns False when connection_ready is not set."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = False
        # _connection_ready is not set

        assert ws_manager.is_connected is False

    def test_is_connected_returns_true_when_all_conditions_met(self, ws_manager):
        """Test is_connected returns True when all conditions are met."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = False
        ws_manager._connection_ready.set()
        # shutdown_event is not set

        assert ws_manager.is_connected is True

    async def test_establish_connection_calls_factory_and_connect(self, ws_manager, mock_ws_transport_factory,
                                                                  mock_websocket_transport):
        """Test _establish_connection calls factory and transport connect."""
        await ws_manager._establish_connection()

        mock_ws_transport_factory.create_transport.assert_called_once()
        mock_websocket_transport.connect.assert_called_once()
        assert ws_manager._transport == mock_websocket_transport

    async def test_stop_sets_shutdown_event(self, ws_manager):
        """Test stop method sets shutdown event."""
        await ws_manager.stop()
        assert ws_manager._shutdown_event.is_set()

    async def test_stop_does_not_wait_out_the_ping_interval(self, ws_manager, caplog):
        """Shutdown must interrupt the ping wait instead of sitting through it.

        The loop used to sleep the whole interval before rechecking shutdown, while
        stop() waits only five seconds - so every ordinary shutdown ended in a forced
        cancellation and a warning about a platform that had done nothing wrong.
        """
        ws_manager.ping_interval = 30.0
        await ws_manager.start()

        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(ws_manager.stop(), timeout=1.0)

        assert ws_manager._websocket_task.done()
        assert not ws_manager._websocket_task.cancelled()
        assert not caplog.records

    async def test_stop_closes_the_transport(self, ws_manager, mock_websocket_transport):
        """The transport is closed on the way out, not merely abandoned."""
        ws_manager.ping_interval = 30.0
        await ws_manager.start()

        await asyncio.wait_for(ws_manager.stop(), timeout=1.0)

        mock_websocket_transport.close.assert_awaited_once()

    async def test_ping_still_fires_while_running(
        self, ws_manager, mock_websocket_transport
    ):
        """Making the wait interruptible must not stop it from pinging."""
        await ws_manager.start()
        await asyncio.sleep(ws_manager.ping_interval * 2.5)

        assert mock_websocket_transport.websocket.ping.await_count >= 2

        await ws_manager.stop()
