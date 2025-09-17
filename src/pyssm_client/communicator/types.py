"""WebSocket communication types and enums."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Union


class MessageType(Enum):
    """WebSocket message types."""

    TEXT = "text"
    BINARY = "binary"
    CLOSE = "close"
    ERROR = "error"


class ConnectionState(Enum):
    """WebSocket connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class WebSocketMessage:
    """WebSocket message container."""

    message_type: MessageType
    data: Union[str, bytes]
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class WebSocketConfig:
    """WebSocket connection configuration."""

    url: str
    token: str
    ping_interval: float = 30.0  # seconds
    ping_timeout: float = 10.0  # seconds
    connect_timeout: float = 30.0  # seconds
    max_message_size: int = 1024 * 1024  # 1MB
    max_frame_size: int = 128 * 1024  # 128KB frame size limit (generous for base64)
    max_queue_size: int = 100
    retry_attempts: int = 3
    retry_delay: float = 1.0  # seconds

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not self.url:
            raise ValueError("WebSocket URL cannot be empty")
        if not self.token:
            raise ValueError("WebSocket token cannot be empty")
        if self.ping_interval <= 0:
            raise ValueError("ping_interval must be positive")
        if self.ping_timeout <= 0:
            raise ValueError("ping_timeout must be positive")
        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")


# Type aliases for callback functions
MessageHandler = Callable[[WebSocketMessage], None]
ErrorHandler = Callable[[Exception], None]
ConnectionHandler = Callable[[ConnectionState], None]
