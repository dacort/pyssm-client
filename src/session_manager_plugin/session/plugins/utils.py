"""Plugin utility functions."""

from __future__ import annotations

from typing import List

from ...utils.logging import get_logger
from ..protocols import ISessionPlugin
from .interactive_commands import InteractiveCommandsPlugin
from .port import PortSessionPlugin
from .standard_stream import StandardStreamPlugin


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
    from ..registry import get_session_registry

    registry = get_session_registry()
    plugins = create_default_plugins()

    for plugin in plugins:
        for session_type in plugin.get_supported_session_types():
            registry.register_plugin(session_type, plugin)

    logger = get_logger(__name__)
    logger.debug("Default session plugins registered")
