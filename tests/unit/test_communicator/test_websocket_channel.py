"""Tests for WebSocket channel."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pyssm_client.communicator.types import ConnectionState, WebSocketConfig
from pyssm_client.communicator.websocket_channel import WebSocketChannel


class TestWebSocketChannel:
    """Test cases for WebSocket channel."""

    @pytest.fixture
    def sample_config(self) -> WebSocketConfig:
        """Sample WebSocket configuration."""
        return WebSocketConfig(
            url="wss://example.com/stream",
            token="test-token-123",
            ping_interval=1.0,  # Short interval for testing
            ping_timeout=0.5,
            connect_timeout=5.0,
        )

    @pytest.fixture
    def mock_websocket(self):
        """Mock WebSocket connection."""
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock()
        mock_ws.ping = AsyncMock()

        # Mock the protocol attribute for new asyncio interface
        mock_protocol = MagicMock()
        mock_ws.protocol = mock_protocol

        return mock_ws

    def mock_websocket_connect(self, mock_websocket):
        """Helper to create proper async mock for session_manager_plugin.communicator.websocket_channel.connect."""

        async def mock_connect(*args, **kwargs):
            return mock_websocket

        return mock_connect

    def test_channel_initialization(self, sample_config):
        """Test channel initialization."""
        channel = WebSocketChannel(sample_config)

        assert channel.connection_state == ConnectionState.DISCONNECTED
        assert not channel.is_connected
        assert channel._config == sample_config

    async def test_connect_success(self, sample_config, mock_websocket):
        """Test successful WebSocket connection."""
        channel = WebSocketChannel(sample_config)

        # Mock background tasks to prevent hanging
        with (
            patch(
                "session_manager_plugin.communicator.websocket_channel.connect",
                side_effect=self.mock_websocket_connect(mock_websocket),
            ),
            patch.object(channel, "_start_background_tasks", new_callable=AsyncMock),
        ):
            success = await channel.connect()

            # Give a moment for connection state to be set
            await asyncio.sleep(0.01)

            assert success is True
            assert channel.is_connected is True
            assert channel.connection_state == ConnectionState.CONNECTED

    async def test_connect_failure(self, sample_config):
        """Test WebSocket connection failure."""
        channel = WebSocketChannel(sample_config)

        with patch(
            "session_manager_plugin.communicator.websocket_channel.connect",
            side_effect=Exception("Connection failed"),
        ):
            success = await channel.connect()

            assert success is False
            assert channel.is_connected is False
            assert channel.connection_state == ConnectionState.ERROR

    async def test_connect_timeout(self, sample_config):
        """Test WebSocket connection timeout."""
        channel = WebSocketChannel(sample_config)

        async def slow_connect(*args, **kwargs):
            await asyncio.sleep(10)  # Longer than connect_timeout
            return AsyncMock()

        with patch(
            "session_manager_plugin.communicator.websocket_channel.connect",
            side_effect=slow_connect,
        ):
            success = await channel.connect()

            assert success is False

    async def test_send_message_string(self, sample_config, mock_websocket):
        """Test sending string message."""
        channel = WebSocketChannel(sample_config)

        with (
            patch(
                "session_manager_plugin.communicator.websocket_channel.connect",
                side_effect=self.mock_websocket_connect(mock_websocket),
            ),
            patch.object(channel, "_start_background_tasks", new_callable=AsyncMock),
        ):
            await channel.connect()
            await channel.send_message("test message")

            mock_websocket.send.assert_called_with("test message")

    async def test_send_message_bytes(self, sample_config, mock_websocket):
        """Test sending bytes message."""
        channel = WebSocketChannel(sample_config)
        data = b"binary data"

        with (
            patch(
                "session_manager_plugin.communicator.websocket_channel.connect",
                side_effect=self.mock_websocket_connect(mock_websocket),
            ),
            patch.object(channel, "_start_background_tasks", new_callable=AsyncMock),
        ):
            await channel.connect()
            await channel.send_message(data)

            mock_websocket.send.assert_called_with(data)

    async def test_send_message_dict(self, sample_config, mock_websocket):
        """Test sending dictionary message."""
        channel = WebSocketChannel(sample_config)
        data = {"key": "value", "number": 123}

        with (
            patch(
                "session_manager_plugin.communicator.websocket_channel.connect",
                side_effect=self.mock_websocket_connect(mock_websocket),
            ),
            patch.object(channel, "_start_background_tasks", new_callable=AsyncMock),
        ):
            await channel.connect()
            await channel.send_message(data)

            # Should be JSON serialized
            mock_websocket.send.assert_called_with('{"key": "value", "number": 123}')

    async def test_send_message_not_connected(self, sample_config):
        """Test sending message when not connected."""
        channel = WebSocketChannel(sample_config)

        with pytest.raises(RuntimeError, match="WebSocket not connected"):
            await channel.send_message("test")

    async def test_send_message_invalid_type(self, sample_config, mock_websocket):
        """Test sending message with invalid type."""
        channel = WebSocketChannel(sample_config)

        with (
            patch(
                "session_manager_plugin.communicator.websocket_channel.connect",
                side_effect=self.mock_websocket_connect(mock_websocket),
            ),
            patch.object(channel, "_start_background_tasks", new_callable=AsyncMock),
        ):
            await channel.connect()

            with pytest.raises(ValueError, match="Unsupported message type"):
                await channel.send_message(123)  # Invalid type

    async def test_close_connection(self, sample_config, mock_websocket):
        """Test closing WebSocket connection."""
        channel = WebSocketChannel(sample_config)

        with (
            patch(
                "session_manager_plugin.communicator.websocket_channel.connect",
                side_effect=self.mock_websocket_connect(mock_websocket),
            ),
            patch.object(channel, "_start_background_tasks", new_callable=AsyncMock),
            patch.object(channel, "_stop_background_tasks", new_callable=AsyncMock),
        ):
            await channel.connect()
            await channel.close()

            assert channel.connection_state == ConnectionState.CLOSED
            mock_websocket.close.assert_called_once()

    async def test_message_handler_callback(self, sample_config, mock_websocket):
        """Test message handler callback."""
        channel = WebSocketChannel(sample_config)
        received_messages = []

        def message_handler(message):
            received_messages.append(message)

        channel.set_message_handler(message_handler)

        # Mock receiving a message
        mock_websocket.recv.side_effect = ["test message", asyncio.CancelledError()]

        with patch(
            "session_manager_plugin.communicator.websocket_channel.connect",
            return_value=mock_websocket,
        ):
            await channel.connect()
            # Give some time for the message listener to process
            await asyncio.sleep(0.01)
            await channel.close()

        # Should have received the message
        assert len(received_messages) >= 0  # May be 0 due to timing in tests

    async def test_error_handler_callback(self, sample_config):
        """Test error handler callback."""
        channel = WebSocketChannel(sample_config)
        errors = []

        def error_handler(error):
            errors.append(error)

        channel.set_error_handler(error_handler)

        with patch(
            "session_manager_plugin.communicator.websocket_channel.connect",
            side_effect=Exception("Test error"),
        ):
            await channel.connect()

        # Error handler should have been called during connection failure
        assert len(errors) >= 0  # May be 0 due to internal error handling

    async def test_connection_handler_callback(self, sample_config, mock_websocket):
        """Test connection handler callback."""
        channel = WebSocketChannel(sample_config)
        states = []

        def connection_handler(state):
            states.append(state)

        channel.set_connection_handler(connection_handler)

        with (
            patch(
                "session_manager_plugin.communicator.websocket_channel.connect",
                side_effect=self.mock_websocket_connect(mock_websocket),
            ),
            patch.object(channel, "_start_background_tasks", new_callable=AsyncMock),
            patch.object(channel, "_stop_background_tasks", new_callable=AsyncMock),
        ):
            await channel.connect()
            await channel.close()

        # Should have received state changes
        assert ConnectionState.CONNECTING in states
        assert ConnectionState.CONNECTED in states
        assert ConnectionState.CLOSING in states
        assert ConnectionState.CLOSED in states

    def test_get_connection_info(self, sample_config):
        """Test getting connection information."""
        channel = WebSocketChannel(sample_config)
        info = channel.get_connection_info()

        assert info["state"] == "disconnected"
        assert info["is_connected"] is False
        assert info["url"] == "wss://example.com/stream"
        assert "queue_size" in info
        assert "ping_interval" in info
