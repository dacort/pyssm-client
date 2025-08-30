"""Session management protocols (interfaces)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Protocol

from .types import ClientConfig, SessionConfig, SessionProperties, SessionStatus


class IDataChannel(Protocol):
    """Interface for data channel operations."""

    async def open(self) -> bool:
        """Open the data channel connection."""
        ...

    async def send_input_data(self, data: bytes) -> None:
        """Send input data through the channel."""
        ...

    async def close(self) -> None:
        """Close the data channel."""
        ...

    @property
    def is_open(self) -> bool:
        """Check if channel is open."""
        ...


class ISession(Protocol):
    """Core session interface."""

    @property
    def session_id(self) -> str:
        """Get session identifier."""
        ...

    @property
    def status(self) -> SessionStatus:
        """Get current session status."""
        ...

    async def execute(self) -> None:
        """Execute the session."""
        ...

    async def open_data_channel(self) -> bool:
        """Open the data channel for this session."""
        ...

    async def terminate_session(self) -> None:
        """Terminate the session."""
        ...

    def set_data_channel(self, data_channel: IDataChannel) -> None:
        """Set the data channel for this session."""
        ...

    def get_session_properties(self) -> SessionProperties:
        """Get session properties for monitoring."""
        ...


class ISessionPlugin(ABC):
    """Abstract base class for session plugins."""

    @abstractmethod
    def get_supported_session_types(self) -> List[str]:
        """Return list of supported session types."""

    @abstractmethod
    async def create_session(
        self, config: SessionConfig, client_config: ClientConfig
    ) -> ISession:
        """Create a new session instance."""

    @abstractmethod
    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration."""


class ISessionRegistry(Protocol):
    """Interface for session plugin registry."""

    def register_plugin(self, session_type: str, plugin: ISessionPlugin) -> None:
        """Register a session plugin for a specific session type."""
        ...

    def get_plugin(self, session_type: str) -> ISessionPlugin | None:
        """Get plugin for a specific session type."""
        ...

    def get_supported_session_types(self) -> List[str]:
        """Get all supported session types."""
        ...

    async def create_session(
        self, config: SessionConfig, client_config: ClientConfig
    ) -> ISession:
        """Create a session using the appropriate plugin."""
        ...
