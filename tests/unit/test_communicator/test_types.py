"""Tests for WebSocket communication types."""

import pytest
import time

from session_manager_plugin.communicator.types import (
    ConnectionState,
    MessageType,
    WebSocketConfig,
    WebSocketMessage,
)


class TestWebSocketConfig:
    """Test cases for WebSocketConfig."""

    def test_valid_config(self):
        """Test creating valid WebSocket configuration."""
        config = WebSocketConfig(url="wss://example.com/stream", token="test-token-123")

        assert config.url == "wss://example.com/stream"
        assert config.token == "test-token-123"
        assert config.ping_interval == 30.0
        assert config.ping_timeout == 10.0
        assert config.connect_timeout == 30.0

    def test_config_with_custom_values(self):
        """Test configuration with custom values."""
        config = WebSocketConfig(
            url="ws://localhost:8080",
            token="custom-token",
            ping_interval=15.0,
            ping_timeout=5.0,
            connect_timeout=60.0,
            max_message_size=2048,
            retry_attempts=5,
        )

        assert config.ping_interval == 15.0
        assert config.ping_timeout == 5.0
        assert config.connect_timeout == 60.0
        assert config.max_message_size == 2048
        assert config.retry_attempts == 5

    def test_empty_url_raises_error(self):
        """Test that empty URL raises ValueError."""
        with pytest.raises(ValueError, match="WebSocket URL cannot be empty"):
            WebSocketConfig(url="", token="test-token")

    def test_empty_token_raises_error(self):
        """Test that empty token raises ValueError."""
        with pytest.raises(ValueError, match="WebSocket token cannot be empty"):
            WebSocketConfig(url="wss://example.com", token="")

    def test_negative_ping_interval_raises_error(self):
        """Test that negative ping_interval raises ValueError."""
        with pytest.raises(ValueError, match="ping_interval must be positive"):
            WebSocketConfig(
                url="wss://example.com", token="test-token", ping_interval=-1.0
            )

    def test_zero_ping_timeout_raises_error(self):
        """Test that zero ping_timeout raises ValueError."""
        with pytest.raises(ValueError, match="ping_timeout must be positive"):
            WebSocketConfig(
                url="wss://example.com", token="test-token", ping_timeout=0.0
            )


class TestWebSocketMessage:
    """Test cases for WebSocketMessage."""

    def test_text_message(self):
        """Test creating text message."""
        message = WebSocketMessage(message_type=MessageType.TEXT, data="Hello, World!")

        assert message.message_type == MessageType.TEXT
        assert message.data == "Hello, World!"
        assert message.timestamp > 0

    def test_binary_message(self):
        """Test creating binary message."""
        data = b"binary data"
        message = WebSocketMessage(message_type=MessageType.BINARY, data=data)

        assert message.message_type == MessageType.BINARY
        assert message.data == data
        assert message.timestamp > 0

    def test_message_with_explicit_timestamp(self):
        """Test message with explicit timestamp."""
        timestamp = time.time()
        message = WebSocketMessage(
            message_type=MessageType.TEXT, data="test", timestamp=timestamp
        )

        assert message.timestamp == timestamp

    def test_timestamp_auto_set(self):
        """Test that timestamp is automatically set."""
        before_time = time.time()
        message = WebSocketMessage(message_type=MessageType.TEXT, data="test")
        after_time = time.time()

        assert before_time <= message.timestamp <= after_time


class TestEnums:
    """Test cases for enums."""

    def test_message_type_values(self):
        """Test MessageType enum values."""
        assert MessageType.TEXT.value == "text"
        assert MessageType.BINARY.value == "binary"
        assert MessageType.CLOSE.value == "close"
        assert MessageType.ERROR.value == "error"

    def test_connection_state_values(self):
        """Test ConnectionState enum values."""
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.CLOSING.value == "closing"
        assert ConnectionState.CLOSED.value == "closed"
        assert ConnectionState.ERROR.value == "error"
