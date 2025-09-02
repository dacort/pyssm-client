"""CLI argument types and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..file_transfer.types import FileTransferDirection, FileTransferEncoding, ChecksumType


@dataclass
class ConnectArguments:
    """CLI arguments for connect command (direct session parameters)."""

    # Required session parameters
    session_id: str
    stream_url: str
    token_value: str

    # Optional session parameters
    target: Optional[str] = None
    document_name: Optional[str] = None
    session_type: str = "Standard_Stream"

    # Client configuration
    client_id: Optional[str] = None

    # Session parameters (JSON string)
    parameters: Optional[str] = None

    # CLI behavior options
    profile: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None

    # Debug and logging
    verbose: bool = False
    log_file: Optional[str] = None

    # Additional client functionality
    initial_input: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConnectArguments:
        """Create ConnectArguments from dictionary (typically from AWS CLI)."""
        return cls(
            session_id=data.get("SessionId") or data.get("sessionId", ""),
            stream_url=data.get("StreamUrl") or data.get("streamUrl", ""),
            token_value=data.get("TokenValue") or data.get("tokenValue", ""),
            target=data.get("target"),
            document_name=data.get("documentName"),
            session_type=data.get("sessionType", "Standard_Stream"),
            client_id=data.get("clientId"),
            parameters=data.get("parameters"),
            profile=data.get("profile"),
            region=data.get("region"),
            endpoint_url=data.get("endpointUrl"),
            verbose=data.get("verbose", False),
            log_file=data.get("logFile"),
            initial_input=data.get("initialInput"),
        )

    def get_parameters_dict(self) -> Dict[str, Any]:
        """Parse parameters JSON string into dictionary."""
        if not self.parameters:
            return {}

        try:
            result = json.loads(self.parameters)
            if not isinstance(result, dict):
                raise ValueError("Parameters must be a JSON object")
            return result
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid parameters JSON: {e}")

    def validate(self) -> List[str]:
        """Validate CLI arguments and return list of errors."""
        errors = []

        if not self.session_id:
            errors.append("sessionId is required")

        if not self.stream_url:
            errors.append("streamUrl is required")

        if not self.token_value:
            errors.append("tokenValue is required")

        # Validate URL format
        if self.stream_url and not (
            self.stream_url.startswith("wss://") or self.stream_url.startswith("ws://")
        ):
            errors.append("streamUrl must be a WebSocket URL (ws:// or wss://)")

        # Validate parameters JSON if provided
        if self.parameters:
            try:
                self.get_parameters_dict()
            except ValueError as e:
                errors.append(str(e))

        return errors


@dataclass
class SSHArguments:
    """CLI arguments for ssh command (AWS SSM integration)."""

    # Required target
    target: str

    # Optional session configuration
    document_name: Optional[str] = None
    session_type: str = "Standard_Stream"

    # AWS configuration
    profile: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None

    # Additional session parameters
    parameters: Optional[Dict[str, Any]] = None

    # Debug and logging
    verbose: bool = False
    log_file: Optional[str] = None

    def validate(self) -> List[str]:
        """Validate SSH arguments."""
        errors = []

        if not self.target:
            errors.append("target is required")

        # Basic target format validation (instance-id, etc.)
        if self.target and not (
            self.target.startswith("i-")
            or self.target.startswith("mi-")  # EC2 instance  # Managed instance
            or self.target.startswith("ssm-")  # Custom target
        ):
            errors.append(
                "target must be a valid instance ID (i-*) or managed instance ID (mi-*)"
            )

        return errors


@dataclass 
class FileCopyArguments:
    """CLI arguments for scp-style file copy operations."""
    
    # Source and destination (scp-style: local_file or target:remote_file)
    source: Optional[str] = None
    destination: Optional[str] = None
    
    # Parsed values (populated during validation)
    target: Optional[str] = None
    local_path: Optional[str] = None
    remote_path: Optional[str] = None
    direction: Optional[FileTransferDirection] = None
    
    # Transfer options
    encoding: FileTransferEncoding = FileTransferEncoding.BASE64
    chunk_size: int = 65536  # 64KB
    verify_checksum: bool = True
    checksum_type: ChecksumType = ChecksumType.MD5
    
    # AWS configuration
    profile: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    
    # Progress and output
    show_progress: bool = True
    quiet: bool = False
    verbose: bool = False
    
    @classmethod
    def from_scp_style(
        cls, 
        source: str, 
        destination: str, 
        **kwargs
    ) -> "FileCopyArguments":
        """Create FileCopyArguments from scp-style source and destination.
        
        Examples:
            ./file.txt i-123:/tmp/file.txt  (upload)
            i-123:/var/log/app.log ./app.log  (download)
        """
        args = cls(source=source, destination=destination, **kwargs)
        return args
    
    def _parse_path(self, path: str) -> tuple[Optional[str], str]:
        """Parse a path into (target, file_path) tuple.
        
        Args:
            path: Either a local path or target:remote_path
            
        Returns:
            (target_id, file_path) where target_id is None for local paths
        """
        if ':' not in path:
            # Local path
            return None, path
            
        # Split on first colon to check if it looks like target:path
        parts = path.split(':', 1)
        if len(parts) != 2:
            # Should not happen since we checked for colon above
            return None, path
            
        potential_target, potential_path = parts
        
        # Check if the part before colon looks like a target ID
        if (potential_target.startswith("i-") 
            or potential_target.startswith("mi-") 
            or potential_target.startswith("ssm-")):
            # This looks like a target:path format
            return potential_target, potential_path
        else:
            # The colon is probably part of a local path (like ./file:with:colons.txt)
            return None, path
    
    def validate(self) -> List[str]:
        """Validate scp-style file copy arguments."""
        errors = []
        
        if not self.source or not self.destination:
            errors.append("Both source and destination are required")
            return errors
            
        try:
            # Parse source and destination paths
            src_target, src_path = self._parse_path(self.source)
            dst_target, dst_path = self._parse_path(self.destination)
            
            # Determine direction and extract target
            if src_target and dst_target:
                # Both remote - not supported
                errors.append("Cannot copy between two remote hosts")
            elif src_target and not dst_target:
                # Remote to local (download)
                self.direction = FileTransferDirection.DOWNLOAD
                self.target = src_target
                self.remote_path = src_path
                self.local_path = dst_path
            elif not src_target and dst_target:
                # Local to remote (upload)
                self.direction = FileTransferDirection.UPLOAD
                self.target = dst_target
                self.local_path = src_path
                self.remote_path = dst_path
            else:
                # Both local - not supported
                errors.append("Cannot copy between two local paths")
                
        except ValueError as e:
            errors.append(str(e))
            # Still continue to parse what we can for testing purposes
            
        # Validate local file exists for upload (only if we have a local path)
        if (self.direction == FileTransferDirection.UPLOAD 
            and self.local_path 
            and not Path(self.local_path).exists()):
            errors.append(f"local file not found: {self.local_path}")
        
        # Validate chunk size
        if self.chunk_size <= 0:
            errors.append("chunk_size must be positive")
        
        return errors
    
    @property
    def is_upload(self) -> bool:
        """Check if this is an upload operation."""
        return self.direction == FileTransferDirection.UPLOAD
        
    @property
    def is_download(self) -> bool:
        """Check if this is a download operation."""
        return self.direction == FileTransferDirection.DOWNLOAD
