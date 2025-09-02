"""File transfer session plugin."""

from __future__ import annotations

from typing import List

from ...utils.logging import get_logger
from ..protocols import ISession, ISessionPlugin
from ..session import Session
from ..types import ClientConfig, SessionConfig
from ...file_transfer.types import FileTransferOptions


class FileTransferSession(Session):
    """Specialized session for file transfer operations."""
    
    def __init__(self, config: SessionConfig, client_config: ClientConfig) -> None:
        """Initialize file transfer session."""
        super().__init__(config, client_config)
        self.transfer_options: FileTransferOptions | None = None
        
    def set_transfer_options(self, options: FileTransferOptions) -> None:
        """Set file transfer specific options."""
        self.transfer_options = options
        
    async def execute(self) -> None:
        """Execute file transfer session."""
        self.logger.debug(f"Starting file transfer session: {self.config.session_id}")
        
        # File transfer sessions don't auto-execute like standard streams
        # Instead, they wait for explicit file transfer commands
        self.logger.debug("File transfer session ready for operations")
        

class FileTransferSessionPlugin(ISessionPlugin):
    """Plugin for file transfer session type."""
    
    def __init__(self) -> None:
        """Initialize file transfer plugin."""
        self._logger = get_logger(__name__)
        
    def get_supported_session_types(self) -> List[str]:
        """Return supported session types."""
        return ["FileTransfer"]
        
    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration for file transfer.
        
        Args:
            config: Session configuration to validate
            
        Returns:
            True if validation passes, False otherwise
        """
        # Basic validation
        if not config.session_id:
            self._logger.error("Session ID cannot be empty")
            return False
            
        if not config.stream_url:
            self._logger.error("Stream URL cannot be empty")  
            return False
            
        if not config.token_value:
            self._logger.error("Token value cannot be empty")
            return False
            
        # File transfer sessions use Standard_Stream document
        self._logger.debug(f"File transfer session validation passed: {config.session_id}")
        return True
        
    async def create_session(
        self, config: SessionConfig, client_config: ClientConfig
    ) -> ISession:
        """Create a file transfer session.
        
        Args:
            config: Session configuration
            client_config: Client configuration
            
        Returns:
            New file transfer session instance
        """
        self._logger.debug(f"Creating file transfer session: {config.session_id}")
        return FileTransferSession(config, client_config)