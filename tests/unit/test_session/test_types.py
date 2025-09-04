"""Tests for session types."""

import pytest
from datetime import datetime

from pyssm_client.session.types import (
    SessionConfig,
    ClientConfig,
    SessionType,
    SessionStatus,
    SessionProperties,
)


class TestSessionConfig:
    """Test cases for SessionConfig."""

    def test_valid_config(self):
        """Test creating valid session configuration."""
        config = SessionConfig(
            session_id="test-session",
            stream_url="wss://example.com/stream",
            token_value="test-token",
        )

        assert config.session_id == "test-session"
        assert config.stream_url == "wss://example.com/stream"
        assert config.token_value == "test-token"
        assert config.parameters == {}

    def test_config_with_optional_fields(self):
        """Test configuration with optional fields."""
        config = SessionConfig(
            session_id="test-session",
            stream_url="wss://example.com/stream",
            token_value="test-token",
            target="i-1234567890abcdef0",
            document_name="SSM-SessionManagerRunShell",
            parameters={"shellProfile": {"linux": "bash"}},
        )

        assert config.target == "i-1234567890abcdef0"
        assert config.document_name == "SSM-SessionManagerRunShell"
        assert config.parameters["shellProfile"]["linux"] == "bash"

    def test_empty_session_id_raises_error(self):
        """Test that empty session_id raises ValueError."""
        with pytest.raises(ValueError, match="session_id cannot be empty"):
            SessionConfig(
                session_id="",
                stream_url="wss://example.com/stream",
                token_value="test-token",
            )

    def test_empty_stream_url_raises_error(self):
        """Test that empty stream_url raises ValueError."""
        with pytest.raises(ValueError, match="stream_url cannot be empty"):
            SessionConfig(
                session_id="test-session", stream_url="", token_value="test-token"
            )

    def test_empty_token_value_raises_error(self):
        """Test that empty token_value raises ValueError."""
        with pytest.raises(ValueError, match="token_value cannot be empty"):
            SessionConfig(
                session_id="test-session",
                stream_url="wss://example.com/stream",
                token_value="",
            )


class TestClientConfig:
    """Test cases for ClientConfig."""

    def test_default_client_id_generated(self):
        """Test that client_id is auto-generated if not provided."""
        config = ClientConfig(session_type=SessionType.STANDARD_STREAM)

        assert config.session_type == SessionType.STANDARD_STREAM
        assert config.client_id is not None
        assert len(config.client_id) > 0

    def test_explicit_client_id(self):
        """Test with explicit client_id."""
        config = ClientConfig(
            session_type=SessionType.PORT, client_id="custom-client-123"
        )

        assert config.client_id == "custom-client-123"


class TestSessionType:
    """Test cases for SessionType enum."""

    def test_session_type_values(self):
        """Test session type enum values."""
        assert SessionType.STANDARD_STREAM.value == "Standard_Stream"
        assert SessionType.PORT.value == "Port"
        assert SessionType.INTERACTIVE_COMMANDS.value == "InteractiveCommands"

    def test_session_type_from_string(self):
        """Test creating session type from string."""
        session_type = SessionType("Standard_Stream")
        assert session_type == SessionType.STANDARD_STREAM


class TestSessionStatus:
    """Test cases for SessionStatus enum."""

    def test_session_status_values(self):
        """Test session status enum values."""
        assert SessionStatus.CREATED.value == "Created"
        assert SessionStatus.CONNECTED.value == "Connected"
        assert SessionStatus.TERMINATING.value == "Terminating"
        assert SessionStatus.TERMINATED.value == "Terminated"
        assert SessionStatus.FAILED.value == "Failed"


class TestSessionProperties:
    """Test cases for SessionProperties."""

    def test_session_properties_to_dict(self):
        """Test converting session properties to dictionary."""
        created_time = datetime.now()

        props = SessionProperties(
            session_id="test-session",
            status=SessionStatus.CONNECTED,
            session_type=SessionType.STANDARD_STREAM,
            created_at=created_time,
            client_id="client-123",
            stream_url="wss://example.com/stream",
            has_data_channel=True,
            data_channel_open=True,
        )

        result = props.to_dict()

        assert result["session_id"] == "test-session"
        assert result["status"] == "Connected"
        assert result["session_type"] == "Standard_Stream"
        assert result["created_at"] == created_time.isoformat()
        assert result["client_id"] == "client-123"
        assert result["stream_url"] == "wss://example.com/stream"
        assert result["has_data_channel"] is True
        assert result["data_channel_open"] is True
