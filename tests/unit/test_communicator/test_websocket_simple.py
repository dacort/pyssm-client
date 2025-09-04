"""Simplified tests for WebSocket channel focusing on core functionality."""

import pytest
from unittest.mock import MagicMock, patch

from pyssm_client.communicator.types import ConnectionState, WebSocketConfig
from pyssm_client.communicator.websocket_channel import WebSocketChannel


class TestWebSocketChannelCore:
    """Test cases for WebSocket channel core functionality."""

    @pytest.fixture
    def sample_config(self) -> WebSocketConfig:
        """Sample WebSocket configuration."""
        return WebSocketConfig(
            url="wss://example.com/stream",
            token="test-token-123",
        )

    def test_channel_initialization(self, sample_config):
        """Test channel initialization."""
        channel = WebSocketChannel(sample_config)

        assert channel.connection_state == ConnectionState.DISCONNECTED
        assert not channel.is_connected
        assert channel._config == sample_config

    def test_handler_setters(self, sample_config):
        """Test setting various handlers."""
        channel = WebSocketChannel(sample_config)

        message_handler = MagicMock()
        error_handler = MagicMock()
        connection_handler = MagicMock()

        channel.set_message_handler(message_handler)
        channel.set_error_handler(error_handler)
        channel.set_connection_handler(connection_handler)

        assert channel._message_handler == message_handler
        assert channel._error_handler == error_handler
        assert channel._connection_handler == connection_handler

    async def test_send_message_not_connected(self, sample_config):
        """Test sending message when not connected."""
        channel = WebSocketChannel(sample_config)

        with pytest.raises(RuntimeError, match="WebSocket not connected"):
            await channel.send_message("test")

    def test_get_connection_info(self, sample_config):
        """Test getting connection information."""
        channel = WebSocketChannel(sample_config)
        info = channel.get_connection_info()

        assert info["state"] == "disconnected"
        assert info["is_connected"] is False
        assert info["url"] == "wss://example.com/stream"
        assert "queue_size" in info
        assert "ping_interval" in info

    async def test_connect_failure_handling(self, sample_config):
        """Test connection failure is handled gracefully."""
        channel = WebSocketChannel(sample_config)

        with patch(
            "pyssm_client.communicator.websocket_channel.connect",
            side_effect=Exception("Connection failed"),
        ):
            success = await channel.connect()

            assert success is False
            assert channel.connection_state == ConnectionState.ERROR

    async def test_close_when_not_connected(self, sample_config):
        """Test closing when not connected."""
        channel = WebSocketChannel(sample_config)

        # Should not raise an exception
        await channel.close()
        assert channel.connection_state == ConnectionState.CLOSED
