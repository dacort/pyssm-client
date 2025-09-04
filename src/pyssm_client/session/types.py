"""Session data types and enums."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class SessionType(Enum):
    """Supported session types."""

    STANDARD_STREAM = "Standard_Stream"
    PORT = "Port"
    INTERACTIVE_COMMANDS = "InteractiveCommands"


class SessionStatus(Enum):
    """Session status states."""

    CREATED = "Created"
    CONNECTED = "Connected"
    TERMINATING = "Terminating"
    TERMINATED = "Terminated"
    FAILED = "Failed"


@dataclass
class SessionConfig:
    """Configuration for session initialization."""

    session_id: str
    stream_url: str
    token_value: str
    target: Optional[str] = None
    document_name: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.session_id:
            raise ValueError("session_id cannot be empty")
        if not self.stream_url:
            raise ValueError("stream_url cannot be empty")
        if not self.token_value:
            raise ValueError("token_value cannot be empty")


@dataclass
class ClientConfig:
    """Client configuration for session."""

    session_type: SessionType
    client_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    input_stream_message_handler: Optional[Any] = None
    output_stream_message_handler: Optional[Any] = None


@dataclass
class SessionProperties:
    """Session properties for monitoring and debugging."""

    session_id: str
    status: SessionStatus
    session_type: SessionType
    created_at: datetime
    client_id: str
    stream_url: str
    has_data_channel: bool = False
    data_channel_open: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "session_type": self.session_type.value,
            "created_at": self.created_at.isoformat(),
            "client_id": self.client_id,
            "stream_url": self.stream_url,
            "has_data_channel": self.has_data_channel,
            "data_channel_open": self.data_channel_open,
        }
