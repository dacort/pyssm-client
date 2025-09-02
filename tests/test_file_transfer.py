"""Integration tests for file transfer functionality."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from src.session_manager_plugin.file_transfer.client import FileTransferClient
from src.session_manager_plugin.file_transfer.types import (
    FileTransferOptions, 
    FileTransferEncoding, 
    ChecksumType, 
    FileChecksum
)
from src.session_manager_plugin.cli.types import FileCopyArguments
from src.session_manager_plugin.cli.main import SessionManagerPlugin


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
        assert options.chunk_size == 65536
        assert options.encoding == FileTransferEncoding.BASE64
        
        # Invalid chunk size
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            FileTransferOptions(chunk_size=0)


class TestFileCopyArguments:
    """Test CLI argument parsing and validation."""
    
    def test_direction_auto_detection_upload(self, tmp_path):
        """Test auto-detection of upload direction."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("test content")
        
        args = FileCopyArguments(
            target="i-1234567890abcdef0",
            local_path=str(test_file)
        )
        errors = args.validate()
        assert not errors  # Should validate without errors after auto-detection
        assert args.direction.value == "upload"
        assert args.remote_path == "file.txt"  # Should auto-set to basename
        
    def test_direction_auto_detection_download(self):
        """Test auto-detection of download direction.""" 
        args = FileCopyArguments(
            target="i-1234567890abcdef0",
            remote_path="/var/log/app.log"
        )
        errors = args.validate()
        assert not errors
        assert args.direction.value == "download"
        assert args.local_path == "app.log"  # Should auto-set to basename
        
    def test_invalid_target_validation(self):
        """Test target ID validation."""
        args = FileCopyArguments(
            target="invalid-target",
            remote_path="/var/log/app.log"  # Use remote path to avoid file existence check
        )
        errors = args.validate()
        assert len(errors) == 1
        assert "target must be a valid instance ID" in errors[0]
        
    def test_missing_paths_validation(self):
        """Test validation when no paths provided."""
        args = FileCopyArguments(target="i-1234567890abcdef0")
        errors = args.validate()
        assert len(errors) == 1
        assert "at least one of local_path or remote_path is required" in errors[0]


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
            "StreamUrl": "wss://ssm.us-east-1.amazonaws.com/v1/data-channel/session-12345"
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
            "stream_url": "wss://ssm.us-east-1.amazonaws.com/v1/data-channel/session-12345"
        }
        
        # Mock the import within the function
        with patch("src.session_manager_plugin.communicator.data_channel.SessionDataChannel") as mock_channel_class:
            mock_channel = Mock()
            mock_channel.open = AsyncMock(return_value=True)
            mock_channel_class.return_value = mock_channel
            
            data_channel = await client._setup_data_channel(session_data)
            
            # Verify data channel was configured
            assert data_channel is mock_channel
            mock_channel.set_input_handler.assert_called_once()
            mock_channel.set_closed_handler.assert_called_once()
            mock_channel.open.assert_awaited_once()


class TestSessionManagerPluginIntegration:
    """Test SessionManagerPlugin file transfer methods."""
    
    @pytest.mark.asyncio
    async def test_upload_file_method(self):
        """Test SessionManagerPlugin.upload_file method."""
        plugin = SessionManagerPlugin()
        
        with patch("src.session_manager_plugin.file_transfer.client.FileTransferClient") as mock_client_class:
            mock_client = Mock()
            mock_client.upload_file = AsyncMock(return_value=True)
            mock_client_class.return_value = mock_client
            
            result = await plugin.upload_file(
                local_path="/path/to/file.txt",
                remote_path="/tmp/file.txt",
                target="i-1234567890abcdef0"
            )
            
            assert result is True
            mock_client.upload_file.assert_awaited_once()
            
    @pytest.mark.asyncio
    async def test_download_file_method(self):
        """Test SessionManagerPlugin.download_file method."""
        plugin = SessionManagerPlugin()
        
        with patch("src.session_manager_plugin.file_transfer.client.FileTransferClient") as mock_client_class:
            mock_client = Mock()
            mock_client.download_file = AsyncMock(return_value=True)
            mock_client_class.return_value = mock_client
            
            result = await plugin.download_file(
                remote_path="/var/log/app.log",
                local_path="/tmp/app.log",
                target="i-1234567890abcdef0"
            )
            
            assert result is True
            mock_client.download_file.assert_awaited_once()
            
    @pytest.mark.asyncio
    async def test_verify_remote_file_method(self):
        """Test SessionManagerPlugin.verify_remote_file method."""
        plugin = SessionManagerPlugin()
        
        with patch("src.session_manager_plugin.file_transfer.client.FileTransferClient") as mock_client_class:
            mock_client = Mock()
            mock_client.verify_remote_file = AsyncMock(return_value="abc123def456")
            mock_client_class.return_value = mock_client
            
            result = await plugin.verify_remote_file(
                remote_path="/var/log/app.log",
                target="i-1234567890abcdef0"
            )
            
            assert result == "abc123def456"
            mock_client.verify_remote_file.assert_awaited_once()


class TestProgressReporting:
    """Test progress reporting functionality."""
    
    def test_progress_callback_invocation(self):
        """Test that progress callbacks are invoked correctly."""
        progress_calls = []
        
        def progress_callback(bytes_transferred: int, total_bytes: int):
            progress_calls.append((bytes_transferred, total_bytes))
            
        options = FileTransferOptions(
            progress_callback=progress_callback
        )
        
        # Simulate progress updates
        if options.progress_callback:
            options.progress_callback(1024, 4096)
            options.progress_callback(2048, 4096) 
            options.progress_callback(4096, 4096)
            
        assert len(progress_calls) == 3
        assert progress_calls[0] == (1024, 4096)
        assert progress_calls[1] == (2048, 4096)
        assert progress_calls[2] == (4096, 4096)