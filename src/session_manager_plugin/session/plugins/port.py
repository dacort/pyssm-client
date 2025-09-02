"""Port forwarding session plugin."""

from __future__ import annotations

from typing import List

from ..protocols import ISession
from ..session import Session
from ..types import ClientConfig, SessionConfig
from .base import BaseSessionPlugin


class PortSessionPlugin(BaseSessionPlugin):
    """Plugin for Port session type (port forwarding)."""

    def get_supported_session_types(self) -> List[str]:
        """Return supported session types."""
        return ["Port"]

    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration for Port sessions.

        Args:
            config: Session configuration to validate

        Returns:
            True if validation passes, False otherwise
        """
        if not self._validate_basic_properties(config):
            return False

        # Port sessions require port number parameter
        if not config.parameters:
            self._logger.error("Port session missing parameters")
            return False

        port_number = config.parameters.get("portNumber")
        if not port_number:
            self._logger.error("Port session missing portNumber parameter")
            return False

        # Validate port number
        try:
            port = int(port_number)
            if not (1 <= port <= 65535):
                self._logger.error(f"Invalid port number: {port}")
                return False
        except (ValueError, TypeError):
            self._logger.error(f"Invalid port number format: {port_number}")
            return False

        self._logger.debug(f"Port session validation passed: {config.session_id}")
        return True

    async def create_session(
        self, config: SessionConfig, client_config: ClientConfig
    ) -> ISession:
        """Create a port forwarding session.

        Args:
            config: Session configuration
            client_config: Client configuration

        Returns:
            New port session instance
        """
        self._logger.debug(
            f"Creating port session: {config.session_id} "
            f"with port {config.parameters.get('portNumber')}"
        )
        return Session(config, client_config)