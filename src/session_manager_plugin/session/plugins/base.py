"""Base session plugin implementation."""

from __future__ import annotations

from typing import List

from ...utils.logging import get_logger
from ..protocols import ISession, ISessionPlugin
from ..session import Session
from ..types import ClientConfig, SessionConfig


class BaseSessionPlugin(ISessionPlugin):
    """Base class for session plugins with common functionality."""

    def __init__(self) -> None:
        """Initialize base plugin."""
        self._logger = get_logger(__name__)

    def _validate_basic_properties(self, config: SessionConfig) -> bool:
        """Validate basic session properties common to all session types.

        Args:
            config: Session configuration to validate

        Returns:
            True if basic validation passes, False otherwise
        """
        if not config.session_id:
            self._logger.error("Session ID cannot be empty")
            return False

        if not config.stream_url:
            self._logger.error("Stream URL cannot be empty")
            return False

        if not config.token_value:
            self._logger.error("Token value cannot be empty")
            return False

        return True

    async def create_session(
        self, config: SessionConfig, client_config: ClientConfig
    ) -> ISession:
        """Create a session instance (default implementation).

        Args:
            config: Session configuration
            client_config: Client configuration

        Returns:
            New session instance
        """
        self._logger.debug(f"Creating session: {config.session_id}")
        return Session(config, client_config)