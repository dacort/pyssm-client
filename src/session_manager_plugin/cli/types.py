"""CLI argument types and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


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
