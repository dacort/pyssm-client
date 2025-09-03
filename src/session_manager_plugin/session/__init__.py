"""Session management module."""

# Core session components - safe to import directly
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

# Note: register_default_plugins NOT imported here to avoid circular dependency
# Import it directly from .plugins.utils when needed

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
]
