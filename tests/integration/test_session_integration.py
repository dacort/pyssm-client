"""Integration tests for session management."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from session_manager_plugin.cli.main import SessionManagerPlugin
from session_manager_plugin.cli.types import ConnectArguments
from session_manager_plugin.session.types import ClientConfig, SessionConfig, SessionType


class TestSessionIntegration:
    """Integration tests for complete session workflow."""

    @pytest.fixture
    def sample_cli_args(self) -> ConnectArguments:
        """Sample CLI arguments for testing."""
        return ConnectArguments(
            session_id="test-session-123",
            stream_url="wss://example.com/stream",
            token_value="test-token-456",
            session_type="Standard_Stream",
        )

    @pytest.fixture
    def mock_websocket(self):
        """Mock WebSocket for testing."""
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(return_value="test message")
        mock_ws.ping = AsyncMock(return_value=AsyncMock())

        # Mock the protocol attribute for new asyncio interface
        mock_protocol = MagicMock()
        mock_ws.protocol = mock_protocol

        return mock_ws

    def mock_websocket_connect(self, mock_websocket):
        """Helper to create proper async mock for websockets.connect."""

        async def mock_connect(*args, **kwargs):
            return mock_websocket

        return mock_connect

    async def test_session_creation_flow(self, sample_cli_args, mock_websocket):
        """Test complete session creation and initialization."""
        plugin = SessionManagerPlugin()

        # Test configuration creation
        session_config = plugin._create_session_config(sample_cli_args)

        assert session_config.session_id == "test-session-123"
        assert session_config.stream_url == "wss://example.com/stream"
        assert session_config.token_value == "test-token-456"

        # Test client configuration
        client_config = plugin._create_client_config(sample_cli_args)
        assert client_config.session_type == SessionType.STANDARD_STREAM

    async def test_data_channel_integration(self, sample_cli_args):
        """Test data channel creation and configuration."""
        plugin = SessionManagerPlugin()

        data_channel = await plugin._create_data_channel(sample_cli_args)

        assert data_channel is not None
        assert not data_channel.is_open  # Not connected yet

    async def test_argument_validation(self):
        """Test CLI argument validation."""
        # Invalid args - missing required fields
        invalid_args = ConnectArguments(
            session_id="", stream_url="", token_value=""
        )

        errors = invalid_args.validate()
        assert len(errors) >= 3  # Should have multiple validation errors
        assert "sessionId is required" in errors
        assert "streamUrl is required" in errors
        assert "tokenValue is required" in errors

    async def test_valid_argument_validation(self, sample_cli_args):
        """Test validation with valid arguments."""
        errors = sample_cli_args.validate()
        assert len(errors) == 0

    async def test_parameters_json_parsing(self):
        """Test JSON parameter parsing."""
        args = ConnectArguments(
            session_id="test",
            stream_url="wss://test.com",
            token_value="token",
            parameters='{"key": "value", "number": 123}',
        )

        params = args.get_parameters_dict()
        assert params == {"key": "value", "number": 123}

    async def test_invalid_parameters_json(self):
        """Test invalid JSON parameter handling."""
        args = ConnectArguments(
            session_id="test",
            stream_url="wss://test.com",
            token_value="token",
            parameters='{"invalid": json}',  # Invalid JSON
        )

        errors = args.validate()
        assert any("Invalid parameters JSON" in error for error in errors)

    async def test_plugin_registration(self):
        """Test session plugin registration."""
        plugin = SessionManagerPlugin()

        # Before registration, registry should be empty or have minimal plugins
        initial_plugin_count = len(plugin._session_handler._registry._plugins)

        # Register plugins
        await plugin._register_session_plugins()

        # After registration, should have at least the Standard_Stream plugin
        final_plugin_count = len(plugin._session_handler._registry._plugins)
        assert final_plugin_count > initial_plugin_count

        # Check that Standard_Stream plugin is registered
        registry = plugin._session_handler._registry
        supported_types = registry.get_supported_session_types()
        assert "Standard_Stream" in supported_types

    async def test_session_config_creation(self, sample_cli_args):
        """Test session configuration creation from CLI args."""
        plugin = SessionManagerPlugin()

        session_config = plugin._create_session_config(sample_cli_args)

        assert isinstance(session_config, SessionConfig)
        assert session_config.session_id == sample_cli_args.session_id
        assert session_config.stream_url == sample_cli_args.stream_url
        assert session_config.token_value == sample_cli_args.token_value
        assert session_config.target == sample_cli_args.target
        assert session_config.document_name == sample_cli_args.document_name

    async def test_client_config_creation(self, sample_cli_args):
        """Test client configuration creation from CLI args."""
        plugin = SessionManagerPlugin()

        client_config = plugin._create_client_config(sample_cli_args)

        assert isinstance(client_config, ClientConfig)
        assert client_config.session_type == SessionType.STANDARD_STREAM
        # client_id defaults to empty string when None is provided
        expected_client_id = sample_cli_args.client_id or ""
        assert client_config.client_id == expected_client_id

    async def test_invalid_session_type(self):
        """Test handling of invalid session type."""
        plugin = SessionManagerPlugin()

        invalid_args = ConnectArguments(
            session_id="test",
            stream_url="wss://test.com",
            token_value="token",
            session_type="InvalidType",
        )

        with pytest.raises(ValueError, match="Unsupported session type"):
            plugin._create_client_config(invalid_args)

    async def test_data_channel_configuration(self, sample_cli_args, mock_websocket):
        """Test data channel handler configuration."""
        plugin = SessionManagerPlugin()

        # Create data channel
        data_channel = await plugin._create_data_channel(sample_cli_args)

        # Verify handlers are set
        assert data_channel._input_handler is not None
        assert data_channel._output_handler is not None

    @pytest.mark.asyncio
    async def test_shutdown_event_handling(self):
        """Test shutdown event and cleanup."""
        plugin = SessionManagerPlugin()

        # Test that shutdown event can be set and waited for
        shutdown_task = asyncio.create_task(plugin._shutdown_event.wait())

        # Trigger shutdown
        await plugin._initiate_shutdown()

        # Should complete quickly now
        done, pending = await asyncio.wait([shutdown_task], timeout=0.1)
        assert len(done) == 1  # Task should be complete
        assert len(pending) == 0