"""Core session implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..utils.logging import get_logger
from .protocols import IDataChannel, ISession
from .types import ClientConfig, SessionConfig, SessionProperties, SessionStatus


class Session(ISession):
    """Core session implementation following AWS Session Manager patterns."""

    def __init__(self, config: SessionConfig, client_config: ClientConfig) -> None:
        """Initialize session with configuration."""
        self._logger = get_logger(__name__)
        self._config = config
        self._client_config = client_config
        self._status = SessionStatus.CREATED
        self._data_channel: Optional[IDataChannel] = None
        self._created_at = datetime.now()

        self._logger.debug(
            f"Created session {self.session_id} with type {client_config.session_type.value}"
        )

    @property
    def session_id(self) -> str:
        """Get session identifier."""
        return self._config.session_id

    @property
    def status(self) -> SessionStatus:
        """Get current session status."""
        return self._status

    @property
    def stream_url(self) -> str:
        """Get WebSocket stream URL."""
        return self._config.stream_url

    @property
    def token_value(self) -> str:
        """Get session token."""
        return self._config.token_value

    @property
    def data_channel(self) -> Optional[IDataChannel]:
        """Get data channel instance."""
        return self._data_channel

    async def execute(self) -> None:
        """Execute the session by opening data channel and setting up handlers."""
        try:
            self._logger.debug(f"Executing session {self.session_id}")

            if not await self.open_data_channel():
                self._status = SessionStatus.FAILED
                raise RuntimeError("Failed to open data channel")

            self._status = SessionStatus.CONNECTED
            self._logger.debug(f"Session {self.session_id} connected successfully")

            await self._setup_session_handlers()

        except Exception as e:
            self._logger.error(f"Failed to execute session {self.session_id}: {e}")
            self._status = SessionStatus.FAILED
            raise

    async def open_data_channel(self) -> bool:
        """Open the data channel for this session."""
        if self._data_channel is None:
            self._logger.error("Data channel not initialized")
            return False

        try:
            success = await self._data_channel.open()
            if success:
                self._logger.debug(f"Data channel opened for session {self.session_id}")
            else:
                self._logger.error(
                    f"Failed to open data channel for session {self.session_id}"
                )

            return success

        except Exception as e:
            self._logger.error(f"Error opening data channel: {e}")
            return False

    async def terminate_session(self) -> None:
        """Terminate the session and cleanup resources."""
        try:
            self._logger.debug(f"Terminating session {self.session_id}")
            self._status = SessionStatus.TERMINATING

            if self._data_channel and self._data_channel.is_open:
                await self._data_channel.close()

            self._status = SessionStatus.TERMINATED
            self._logger.debug(f"Session {self.session_id} terminated successfully")

        except Exception as e:
            self._logger.error(f"Error during session termination: {e}")
            self._status = SessionStatus.FAILED
            raise

    def set_data_channel(self, data_channel: IDataChannel) -> None:
        """Set the data channel for this session."""
        self._data_channel = data_channel
        self._logger.debug(f"Data channel set for session {self.session_id}")

    def get_session_properties(self) -> SessionProperties:
        """Get session properties for debugging/monitoring."""
        return SessionProperties(
            session_id=self.session_id,
            status=self.status,
            session_type=self._client_config.session_type,
            created_at=self._created_at,
            client_id=self._client_config.client_id,
            stream_url=self.stream_url,
            has_data_channel=self._data_channel is not None,
            data_channel_open=(
                self._data_channel.is_open if self._data_channel else False
            ),
        )

    @property
    def client_id(self) -> str:
        """Expose client id from client config."""
        return self._client_config.client_id

    async def _setup_session_handlers(self) -> None:
        """Set up session-specific message handlers."""
        session_type = self._client_config.session_type
        self._logger.debug(
            f"Setting up handlers for session type: {session_type.value}"
        )

        # Implementation will be session-type specific
        # This will be expanded based on session type requirements
        match session_type:
            case session_type.STANDARD_STREAM:
                await self._setup_standard_stream_handlers()
            case session_type.PORT:
                await self._setup_port_handlers()
            case session_type.INTERACTIVE_COMMANDS:
                await self._setup_interactive_command_handlers()
            case _:
                self._logger.warning(f"Unknown session type: {session_type}")

    async def _setup_standard_stream_handlers(self) -> None:
        """Setup handlers for standard stream sessions."""
        self._logger.debug("Setting up standard stream handlers")
        # Standard stream setup logic will be implemented here

    async def _setup_port_handlers(self) -> None:
        """Setup handlers for port forwarding sessions."""
        self._logger.debug("Setting up port forwarding handlers")
        # Port forwarding setup logic will be implemented here

    async def _setup_interactive_command_handlers(self) -> None:
        """Setup handlers for interactive command sessions."""
        self._logger.debug("Setting up interactive command handlers")
        # Interactive command setup logic will be implemented here
