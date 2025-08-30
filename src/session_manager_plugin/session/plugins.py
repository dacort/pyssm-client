"""Session type plugins."""

from __future__ import annotations

from typing import List

from ..utils.logging import get_logger
from .protocols import ISession, ISessionPlugin
from .session import Session
from .types import ClientConfig, SessionConfig


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


class StandardStreamPlugin(BaseSessionPlugin):
    """Plugin for Standard_Stream session type."""

    def get_supported_session_types(self) -> List[str]:
        """Return supported session types."""
        return ["Standard_Stream"]

    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration for Standard_Stream.

        Args:
            config: Session configuration to validate

        Returns:
            True if validation passes, False otherwise
        """
        if not self._validate_basic_properties(config):
            return False

        # Standard stream sessions don't require additional validation
        self._logger.debug(
            f"Standard stream session validation passed: {config.session_id}"
        )
        return True


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


def create_default_plugins() -> List[ISessionPlugin]:
    """Create instances of all default session plugins.

    Returns:
        List of plugin instances
    """
    return [
        StandardStreamPlugin(),
        PortSessionPlugin(),
        InteractiveCommandsPlugin(),
    ]


def register_default_plugins() -> None:
    """Register all default plugins with the global registry."""
    from .registry import get_session_registry

    registry = get_session_registry()
    plugins = create_default_plugins()

    for plugin in plugins:
        for session_type in plugin.get_supported_session_types():
            registry.register_plugin(session_type, plugin)

    logger = get_logger(__name__)
    logger.debug("Default session plugins registered")
