"""Tests for WebSocket utilities."""

import pytest

from session_manager_plugin.communicator.utils import (
    build_stream_url,
    create_websocket_config,
    validate_websocket_url,
)


class TestBuildStreamUrl:
    """Test cases for build_stream_url function."""

    def test_build_with_wss_url(self):
        """Test building URL with existing wss:// scheme."""
        result = build_stream_url(
            "wss://example.com/stream", "session-123", "token-456"
        )

        assert result.startswith("wss://example.com/stream")
        assert "sessionId=session-123" in result
        assert "token=token-456" in result

    def test_build_with_https_url(self):
        """Test building URL with https:// scheme conversion."""
        result = build_stream_url(
            "https://example.com/stream", "session-123", "token-456"
        )

        assert result.startswith("wss://example.com/stream")
        assert "sessionId=session-123" in result
        assert "token=token-456" in result

    def test_build_with_http_url(self):
        """Test building URL with http:// scheme conversion."""
        result = build_stream_url(
            "http://example.com/stream", "session-123", "token-456"
        )

        assert result.startswith("ws://example.com/stream")
        assert "sessionId=session-123" in result
        assert "token=token-456" in result

    def test_build_with_bare_hostname(self):
        """Test building URL with bare hostname."""
        result = build_stream_url("example.com/stream", "session-123", "token-456")

        assert result.startswith("wss://example.com/stream")
        assert "sessionId=session-123" in result
        assert "token=token-456" in result

    def test_build_with_special_characters(self):
        """Test building URL with special characters in parameters."""
        result = build_stream_url(
            "wss://example.com/stream", "session@123", "token+456"
        )

        assert "sessionId=session%40123" in result
        assert "token=token%2B456" in result


class TestCreateWebSocketConfig:
    """Test cases for create_websocket_config function."""

    def test_create_with_defaults(self):
        """Test creating config with default values."""
        config = create_websocket_config("wss://example.com", "test-token")

        assert config.url == "wss://example.com"
        assert config.token == "test-token"
        assert config.ping_interval == 30.0
        assert config.ping_timeout == 10.0
        assert config.connect_timeout == 30.0

    def test_create_with_custom_values(self):
        """Test creating config with custom values."""
        config = create_websocket_config(
            "wss://example.com",
            "test-token",
            ping_interval=15.0,
            ping_timeout=5.0,
            max_message_size=2048,
        )

        assert config.ping_interval == 15.0
        assert config.ping_timeout == 5.0
        assert config.max_message_size == 2048

    def test_create_with_extra_kwargs(self):
        """Test creating config with extra keyword arguments."""
        config = create_websocket_config(
            "wss://example.com",
            "test-token",
            retry_attempts=5,
            retry_delay=2.0,
        )

        assert config.retry_attempts == 5
        assert config.retry_delay == 2.0


class TestValidateWebSocketUrl:
    """Test cases for validate_websocket_url function."""

    def test_valid_wss_url(self):
        """Test validation of valid wss:// URL."""
        assert validate_websocket_url("wss://example.com/stream") is True

    def test_valid_ws_url(self):
        """Test validation of valid ws:// URL."""
        assert validate_websocket_url("ws://localhost:8080/stream") is True

    def test_invalid_http_url(self):
        """Test validation of invalid http:// URL."""
        assert validate_websocket_url("http://example.com/stream") is False

    def test_invalid_https_url(self):
        """Test validation of invalid https:// URL."""
        assert validate_websocket_url("https://example.com/stream") is False

    def test_invalid_ftp_url(self):
        """Test validation of invalid ftp:// URL."""
        assert validate_websocket_url("ftp://example.com/file") is False

    def test_invalid_no_scheme(self):
        """Test validation of URL without scheme."""
        assert validate_websocket_url("example.com/stream") is False

    def test_invalid_no_host(self):
        """Test validation of URL without host."""
        assert validate_websocket_url("wss:///stream") is False

    def test_invalid_malformed_url(self):
        """Test validation of malformed URL."""
        assert validate_websocket_url("not-a-url") is False

    def test_invalid_empty_url(self):
        """Test validation of empty URL."""
        assert validate_websocket_url("") is False
