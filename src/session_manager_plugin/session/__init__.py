"""Session management module."""

# Lazy imports to avoid circular dependencies
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .plugins import register_default_plugins
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
    "register_default_plugins",
]


def __getattr__(name: str):
    """Lazy import for session module components."""
    if name == "register_default_plugins":
        from .plugins import register_default_plugins
        return register_default_plugins
    elif name == "get_session_registry":
        from .registry import get_session_registry
        return get_session_registry
    elif name == "reset_session_registry":
        from .registry import reset_session_registry
        return reset_session_registry
    elif name == "Session":
        from .session import Session
        return Session
    elif name == "SessionHandler":
        from .session_handler import SessionHandler
        return SessionHandler
    elif name == "SessionValidationError":
        from .session_handler import SessionValidationError
        return SessionValidationError
    elif name in ("SessionConfig", "ClientConfig", "SessionProperties", "SessionStatus", "SessionType"):
        from .types import (
            ClientConfig,
            SessionConfig,
            SessionProperties,
            SessionStatus,
            SessionType,
        )
        return locals()[name]
    else:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
