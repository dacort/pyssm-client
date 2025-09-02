"""Session management module."""

# Avoid circular import, import where needed instead
# from .plugins import register_default_plugins
from .registry import get_session_registry, reset_session_registry
from .session import Session
from .session_handler import SessionHandler, SessionValidationError
from .types import (
    ClientConfig,
    SessionConfig,
    SessionProperties,
    SessionStatus,
    SessionType,
)

__all__ = [
    "Session",
    "SessionHandler",
    "SessionValidationError",
    "SessionConfig",
    "ClientConfig",
    "SessionProperties",
    "SessionType",
    "SessionStatus",
    "get_session_registry",
    "reset_session_registry",
# "register_default_plugins",  # Import directly where needed to avoid circular imports
]
