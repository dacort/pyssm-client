"""Tests for core Session class."""

import pytest
from unittest.mock import AsyncMock

from pyssm_client.session import (
    Session,
    SessionConfig,
    ClientConfig,
    SessionType,
    SessionStatus,
)


class TestSession:
    """Test cases for Session class."""

    @pytest.fixture
    def sample_config(self) -> SessionConfig:
        """Sample session configuration."""
        return SessionConfig(
            session_id="test-session-123",
            stream_url="wss://example.com/stream",
            token_value="test-token-456",
        )

    @pytest.fixture
    def sample_client_config(self) -> ClientConfig:
        """Sample client configuration."""
        return ClientConfig(
            session_type=SessionType.STANDARD_STREAM, client_id="test-client-789"
        )

    def test_session_initialization(self, sample_config, sample_client_config):
        """Test session initialization with valid configuration."""
        session = Session(sample_config, sample_client_config)

        assert session.session_id == "test-session-123"
        assert session.status == SessionStatus.CREATED
        assert session.stream_url == "wss://example.com/stream"
        assert session.token_value == "test-token-456"
        assert session.data_channel is None

    async def test_session_execute_success(self, sample_config, sample_client_config):
        """Test successful session execution."""
        session = Session(sample_config, sample_client_config)

        # Mock data channel
        mock_data_channel = AsyncMock()
        mock_data_channel.open.return_value = True
        mock_data_channel.is_open = True

        session.set_data_channel(mock_data_channel)

        await session.execute()

        assert session.status == SessionStatus.CONNECTED
        mock_data_channel.open.assert_called_once()

    async def test_session_execute_data_channel_failure(
        self, sample_config, sample_client_config
    ):
        """Test session execution with data channel failure."""
        session = Session(sample_config, sample_client_config)

        # Mock failing data channel
        mock_data_channel = AsyncMock()
        mock_data_channel.open.return_value = False

        session.set_data_channel(mock_data_channel)

        with pytest.raises(RuntimeError, match="Failed to open data channel"):
            await session.execute()

        assert session.status == SessionStatus.FAILED

    async def test_session_execute_no_data_channel(
        self, sample_config, sample_client_config
    ):
        """Test session execution without data channel set."""
        session = Session(sample_config, sample_client_config)

        with pytest.raises(RuntimeError, match="Failed to open data channel"):
            await session.execute()

        assert session.status == SessionStatus.FAILED

    async def test_session_termination(self, sample_config, sample_client_config):
        """Test session termination."""
        session = Session(sample_config, sample_client_config)

        # Mock data channel
        mock_data_channel = AsyncMock()
        mock_data_channel.is_open = True
        session.set_data_channel(mock_data_channel)

        await session.terminate_session()

        assert session.status == SessionStatus.TERMINATED
        mock_data_channel.close.assert_called_once()

    async def test_open_data_channel_success(self, sample_config, sample_client_config):
        """Test successful data channel opening."""
        session = Session(sample_config, sample_client_config)

        mock_data_channel = AsyncMock()
        mock_data_channel.open.return_value = True
        session.set_data_channel(mock_data_channel)

        result = await session.open_data_channel()

        assert result is True
        mock_data_channel.open.assert_called_once()

    async def test_open_data_channel_no_channel(
        self, sample_config, sample_client_config
    ):
        """Test opening data channel when none is set."""
        session = Session(sample_config, sample_client_config)

        result = await session.open_data_channel()

        assert result is False

    def test_get_session_properties(self, sample_config, sample_client_config):
        """Test getting session properties."""
        session = Session(sample_config, sample_client_config)

        properties = session.get_session_properties()

        assert properties.session_id == "test-session-123"
        assert properties.status == SessionStatus.CREATED
        assert properties.session_type == SessionType.STANDARD_STREAM
        assert properties.client_id == "test-client-789"
        assert properties.stream_url == "wss://example.com/stream"
        assert properties.has_data_channel is False
        assert properties.data_channel_open is False

    def test_set_data_channel(self, sample_config, sample_client_config):
        """Test setting data channel."""
        session = Session(sample_config, sample_client_config)
        mock_data_channel = AsyncMock()

        session.set_data_channel(mock_data_channel)

        assert session.data_channel is mock_data_channel
