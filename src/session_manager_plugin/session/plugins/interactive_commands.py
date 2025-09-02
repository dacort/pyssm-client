"""Interactive commands session plugin."""

from __future__ import annotations

from typing import List

from ..protocols import ISession
from ..session import Session
from ..types import ClientConfig, SessionConfig
from .base import BaseSessionPlugin


class InteractiveCommandsPlugin(BaseSessionPlugin):
    """Plugin for InteractiveCommands session type."""

    def get_supported_session_types(self) -> List[str]:
        """Return supported session types."""
        return ["InteractiveCommands"]

    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration for InteractiveCommands.

        Args:
            config: Session configuration to validate

        Returns:
            True if validation passes, False otherwise
        """
        if not self._validate_basic_properties(config):
            return False

        # Interactive command sessions might require specific document name
        if not config.document_name:
            self._logger.warning(
                f"Interactive command session {config.session_id} "
                "has no document name specified"
            )

        self._logger.debug(
            f"Interactive commands session validation passed: {config.session_id}"
        )
        return True

    async def create_session(
        self, config: SessionConfig, client_config: ClientConfig
    ) -> ISession:
        """Create an interactive commands session.

        Args:
            config: Session configuration
            client_config: Client configuration

        Returns:
            New interactive commands session instance
        """
        self._logger.debug(
            f"Creating interactive commands session: {config.session_id}"
        )
        return Session(config, client_config)