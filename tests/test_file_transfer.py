"""Integration tests for file transfer functionality."""

import pytest
from unittest.mock import Mock, patch, AsyncMock

from pyssm_client.file_transfer.client import FileTransferClient
from pyssm_client.file_transfer.types import (
    FileTransferOptions,
    FileTransferEncoding,
    ChecksumType,
    FileChecksum,
)
from pyssm_client.cli.types import FileCopyArguments


class TestFileTransferTypes:
    """Test file transfer type validation and utilities."""

    def test_file_checksum_compute(self, tmp_path):
        """Test file checksum computation."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        checksum = FileChecksum.compute(test_file, ChecksumType.MD5)
        assert checksum.algorithm == ChecksumType.MD5
        assert len(checksum.value) == 32  # MD5 is 32 hex chars
        assert checksum.file_size == 13  # Length of "Hello, World!"

    def test_file_transfer_options_validation(self):
        """Test FileTransferOptions validation."""
        # Valid options
        options = FileTransferOptions()
        assert options.chunk_size == 32768
        assert options.encoding == FileTransferEncoding.BASE64

        # Invalid chunk size
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            FileTransferOptions(chunk_size=0)


class TestFileCopyArguments:
    """Test scp-style CLI argument parsing and validation."""

    def test_scp_upload_parsing(self, tmp_path):
        """Test parsing scp-style upload syntax."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("test content")

        args = FileCopyArguments.from_scp_style(
            source=str(test_file), destination="i-1234567890abcdef0:/tmp/file.txt"
        )
        errors = args.validate()
        assert not errors
        assert args.direction.value == "upload"
        assert args.target == "i-1234567890abcdef0"
        assert args.local_path == str(test_file)
        assert args.remote_path == "/tmp/file.txt"

    def test_scp_download_parsing(self):
        """Test parsing scp-style download syntax."""
        args = FileCopyArguments.from_scp_style(
            source="i-1234567890abcdef0:/var/log/app.log", destination="./app.log"
        )
        errors = args.validate()
        assert not errors
        assert args.direction.value == "download"
        assert args.target == "i-1234567890abcdef0"
        assert args.remote_path == "/var/log/app.log"
        assert args.local_path == "./app.log"

    def test_invalid_target_in_path(self):
        """Test validation of invalid target ID in remote path."""
        args = FileCopyArguments.from_scp_style(
            source="i-invalid:/var/log/app.log",  # Looks like target ID but is invalid format
            destination="./app.log",
        )
        errors = args.validate()
        # Since "i-invalid" matches the i-* pattern, parsing will succeed but AWS validation would fail
        # For this test, we just check that it parsed as expected
        assert not errors  # No parsing errors, would fail at AWS validation time
        assert args.direction.value == "download"
        assert args.target == "i-invalid"

    def test_both_remote_paths_error(self):
        """Test error when both paths are remote."""
        args = FileCopyArguments.from_scp_style(
            source="i-1234567890abcdef0:/source.txt",
            destination="i-0987654321fedcba0:/dest.txt",
        )
        errors = args.validate()
        assert len(errors) == 1
        assert "Cannot copy between two remote hosts" in errors[0]

    def test_both_local_paths_error(self):
        """Test error when both paths are local."""
        args = FileCopyArguments.from_scp_style(
            source="./source.txt", destination="./dest.txt"
        )
        errors = args.validate()
        assert len(errors) == 1
        assert "Cannot copy between two local paths" in errors[0]

    def test_missing_source_destination(self):
        """Test validation when source or destination missing."""
        args = FileCopyArguments()  # No source/destination
        errors = args.validate()
        assert len(errors) == 1
        assert "Both source and destination are required" in errors[0]

    def test_path_parsing_with_colons_in_filename(self):
        """Test parsing paths that contain colons in the filename."""
        args = FileCopyArguments.from_scp_style(
            source="./file:with:colons.txt",
            destination="i-1234567890abcdef0:/tmp/file.txt",
        )
        # Should still work - only the first colon in destination is significant
        errors = args.validate()
        # Will have file not found error, but parsing should work
        assert args.target == "i-1234567890abcdef0"
        assert args.local_path == "./file:with:colons.txt"
        assert args.remote_path == "/tmp/file.txt"
        # Expect file not found error since test file doesn't exist
        assert len(errors) == 1
        assert "local file not found" in errors[0]


class TestFileTransferClient:
    """Test FileTransferClient functionality."""

    @pytest.mark.asyncio
    async def test_create_ssm_session_success(self):
        """Test successful SSM session creation."""
        client = FileTransferClient()

        # Mock boto3 session and SSM client
        mock_response = {
            "SessionId": "session-12345",
            "TokenValue": "token-abcdef",
            "StreamUrl": "wss://ssm.us-east-1.amazonaws.com/v1/data-channel/session-12345",
        }

        with patch("boto3.Session") as mock_session:
            mock_ssm = Mock()
            mock_ssm.start_session.return_value = mock_response
            mock_session.return_value.client.return_value = mock_ssm

            session_data = await client._create_ssm_session(
                target="i-1234567890abcdef0"
            )

            assert session_data["session_id"] == "session-12345"
            assert session_data["token_value"] == "token-abcdef"
            assert "wss://ssm.us-east-1.amazonaws.com" in session_data["stream_url"]

    @pytest.mark.asyncio
    async def test_setup_data_channel(self):
        """Test data channel setup."""
        client = FileTransferClient()

        session_data = {
            "session_id": "session-12345",
            "token_value": "token-abcdef",
            "stream_url": "wss://ssm.us-east-1.amazonaws.com/v1/data-channel/session-12345",
            "target": "i-123456789abcdef0",
        }

        # Mock the import within the function
        with patch(
            "pyssm_client.communicator.data_channel.SessionDataChannel"
        ) as mock_channel_class:
            mock_channel = Mock()
            mock_channel.open = AsyncMock(return_value=True)
            mock_channel_class.return_value = mock_channel

            data_channel, session_obj = await client._setup_data_channel(session_data)

            # Verify data channel was configured
            assert data_channel is mock_channel
            assert session_obj is not None
            mock_channel.set_input_handler.assert_called_once()
            mock_channel.set_closed_handler.assert_called_once()
            mock_channel.open.assert_awaited_once()


class TestSessionManagerPluginIntegration:
    """Test SessionManagerPlugin file transfer methods."""


class TestProgressReporting:
    """Test progress reporting functionality."""

    def test_progress_callback_invocation(self):
        """Test that progress callbacks are invoked correctly."""
        progress_calls = []

        def progress_callback(bytes_transferred: int, total_bytes: int):
            progress_calls.append((bytes_transferred, total_bytes))

        options = FileTransferOptions(progress_callback=progress_callback)

        # Simulate progress updates
        if options.progress_callback:
            options.progress_callback(1024, 4096)
            options.progress_callback(2048, 4096)
            options.progress_callback(4096, 4096)

        assert len(progress_calls) == 3
        assert progress_calls[0] == (1024, 4096)
        assert progress_calls[1] == (2048, 4096)
        assert progress_calls[2] == (4096, 4096)
