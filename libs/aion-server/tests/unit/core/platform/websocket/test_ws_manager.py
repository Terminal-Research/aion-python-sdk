from unittest.mock import MagicMock

import pytest

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
        """Test is_connected returns False when websocket_task is None."""
        assert ws_manager._websocket_task is None
        assert not ws_manager.is_connected

    def test_is_connected_returns_false_when_task_done(self, ws_manager):
        """Test is_connected returns False when websocket task is completed."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = True
        ws_manager._connection_ready.set()

        assert not ws_manager.is_connected

    def test_is_connected_returns_false_when_shutdown_set(self, ws_manager):
        """Test is_connected returns False when shutdown event is set."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = False
        ws_manager._connection_ready.set()
        ws_manager._shutdown_event.set()

        assert not ws_manager.is_connected

    def test_is_connected_returns_false_when_not_ready(self, ws_manager):
        """Test is_connected returns False when connection_ready is not set."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = False
        # _connection_ready is not set

        assert not ws_manager.is_connected

    def test_is_connected_returns_true_when_all_conditions_met(self, ws_manager):
        """Test is_connected returns True when all conditions are met."""
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = False
        ws_manager._connection_ready.set()
        # shutdown_event is not set

        assert ws_manager.is_connected

    @pytest.mark.asyncio
    async def test_establish_connection_calls_factory_and_connect(self, ws_manager, mock_ws_transport_factory,
                                                                  mock_websocket_transport):
        """Test _establish_connection calls factory and transport connect."""
        await ws_manager._establish_connection()

        mock_ws_transport_factory.create_transport.assert_called_once()
        mock_websocket_transport.connect.assert_called_once()
        assert ws_manager._transport == mock_websocket_transport

    @pytest.mark.asyncio
    async def test_stop_sets_shutdown_event(self, ws_manager):
        """Test stop method sets shutdown event."""
        await ws_manager.stop()
        assert ws_manager._shutdown_event.is_set()
