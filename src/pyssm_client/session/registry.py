"""Session plugin registry."""

from __future__ import annotations

from typing import Dict, List

from ..utils.logging import get_logger
from .protocols import ISession, ISessionPlugin, ISessionRegistry
from .types import ClientConfig, SessionConfig


class SessionPluginRegistry(ISessionRegistry):
    """Registry for session plugins with thread-safe operations."""

    def __init__(self) -> None:
        """Initialize the registry."""
        self._logger = get_logger(__name__)
        self._plugins: Dict[str, ISessionPlugin] = {}

    def register_plugin(self, session_type: str, plugin: ISessionPlugin) -> None:
        """Register a session plugin for a specific session type.

        Args:
            session_type: The session type identifier
            plugin: The plugin instance to register
        """
        if session_type in self._plugins:
            self._logger.warning(
                f"Overriding existing plugin for session type: {session_type}"
            )

        self._plugins[session_type] = plugin
        self._logger.debug(f"Registered plugin for session type: {session_type}")

    def get_plugin(self, session_type: str) -> ISessionPlugin | None:
        """Get plugin for a specific session type.

        Args:
            session_type: The session type identifier

        Returns:
            The plugin instance or None if not found
        """
        return self._plugins.get(session_type)

    def get_supported_session_types(self) -> List[str]:
        """Get all supported session types.

        Returns:
            List of supported session type identifiers
        """
        return list(self._plugins.keys())

    async def create_session(
        self, config: SessionConfig, client_config: ClientConfig
    ) -> ISession:
        """Create a session using the appropriate plugin.

        Args:
            config: Session configuration
            client_config: Client configuration

        Returns:
            Created session instance

        Raises:
            ValueError: If no plugin is registered for the session type
            ValueError: If session configuration is invalid
        """
        session_type = client_config.session_type.value

        plugin = self.get_plugin(session_type)
        if not plugin:
            supported_types = self.get_supported_session_types()
            raise ValueError(
                f"No plugin registered for session type '{session_type}'. "
                f"Supported types: {supported_types}"
            )

        if not plugin.validate_session_properties(config):
            raise ValueError(f"Invalid session configuration for type '{session_type}'")

        self._logger.debug(f"Creating session with plugin for type: {session_type}")
        return await plugin.create_session(config, client_config)

    def unregister_plugin(self, session_type: str) -> bool:
        """Unregister a plugin for a session type.

        Args:
            session_type: The session type identifier

        Returns:
            True if plugin was removed, False if not found
        """
        if session_type in self._plugins:
            del self._plugins[session_type]
            self._logger.debug(f"Unregistered plugin for session type: {session_type}")
            return True
        return False

    def clear_plugins(self) -> None:
        """Clear all registered plugins."""
        self._plugins.clear()
        self._logger.debug("Cleared all registered plugins")

    def is_session_type_supported(self, session_type: str) -> bool:
        """Check if a session type is supported.

        Args:
            session_type: The session type identifier

        Returns:
            True if supported, False otherwise
        """
        return session_type in self._plugins


# Global registry instance using singleton pattern
_registry: SessionPluginRegistry | None = None


def get_session_registry() -> SessionPluginRegistry:
    """Get the global session registry instance.

    Returns:
        The global registry instance
    """
    global _registry
    if _registry is None:
        _registry = SessionPluginRegistry()
    return _registry


def reset_session_registry() -> None:
    """Reset the global registry (mainly for testing)."""
    global _registry
    _registry = None
