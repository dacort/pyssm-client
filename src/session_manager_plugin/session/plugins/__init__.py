"""Session plugins package."""

from .base import BaseSessionPlugin
from .interactive_commands import InteractiveCommandsPlugin
from .port import PortSessionPlugin
from .standard_stream import StandardStreamPlugin
from .utils import create_default_plugins, register_default_plugins

# Note: FileTransferSession, FileTransferSessionPlugin NOT imported here
# to avoid circular dependency with file_transfer module
# Import them directly from .file_transfer when needed

__all__ = [
    "BaseSessionPlugin",
    "StandardStreamPlugin",
    "PortSessionPlugin",
    "InteractiveCommandsPlugin",
    "create_default_plugins",
    "register_default_plugins",
]
