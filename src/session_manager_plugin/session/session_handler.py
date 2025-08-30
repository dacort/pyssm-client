"""Session handler for managing session lifecycle."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List
from urllib.parse import urlparse

from ..utils.logging import get_logger
from .protocols import ISession
from .registry import get_session_registry
from .types import ClientConfig, SessionConfig, SessionType


class SessionValidationError(ValueError):
    """Exception raised when session validation fails."""


class SessionHandler:
    """Handles session creation, management, and lifecycle."""

    def __init__(self) -> None:
        """Initialize session handler."""
        self._logger = get_logger(__name__)
        self._registry = get_session_registry()
        self._active_sessions: Dict[str, ISession] = {}

    async def validate_input_and_start_session(
        self, cli_args: Dict[str, Any]
    ) -> ISession:
        """Validate CLI input and start a new session.

        Args:
            cli_args: CLI arguments dictionary

        Returns:
            Created and started session instance

        Raises:
            SessionValidationError: If validation fails
            RuntimeError: If session creation/startup fails
        """
        try:
            # Extract and validate required parameters
            config = self._extract_session_config(cli_args)
            client_config = self._extract_client_config(cli_args)

            # Create session using registry
            session = await self._registry.create_session(config, client_config)

            # Track active session
            self._active_sessions[config.session_id] = session

            # Start session
            await session.execute()

            self._logger.info(f"Session {config.session_id} started successfully")
            return session

        except (ValueError, KeyError) as e:
            raise SessionValidationError(f"Validation failed: {e}") from e
        except Exception as e:
            self._logger.error(f"Failed to start session: {e}")
            raise RuntimeError(f"Session startup failed: {e}") from e

    async def validate_input_and_create_session(
        self, cli_args: Dict[str, Any]
    ) -> ISession:
        """Validate CLI input and create a new session without executing it.

        Args:
            cli_args: CLI arguments dictionary

        Returns:
            Created session instance (not yet executed)

        Raises:
            SessionValidationError: If validation fails
            RuntimeError: If session creation fails
        """
        try:
            # Extract and validate required parameters
            config = self._extract_session_config(cli_args)
            client_config = self._extract_client_config(cli_args)

            # Create session using registry
            session = await self._registry.create_session(config, client_config)

            # Track active session
            self._active_sessions[config.session_id] = session

            self._logger.info(f"Session {config.session_id} created successfully")
            return session

        except (ValueError, KeyError) as e:
            raise SessionValidationError(f"Validation failed: {e}") from e
        except Exception as e:
            self._logger.error(f"Failed to create session: {e}")
            raise RuntimeError(f"Session creation failed: {e}") from e

    def _extract_session_config(self, cli_args: Dict[str, Any]) -> SessionConfig:
        """Extract session configuration from CLI arguments.

        Args:
            cli_args: CLI arguments dictionary

        Returns:
            Validated session configuration

        Raises:
            KeyError: If required fields are missing
            ValueError: If field values are invalid
        """
        # Check for required fields in both PascalCase (AWS output) and camelCase (fallback)
        session_id = cli_args.get("SessionId") or cli_args.get("sessionId")
        stream_url = cli_args.get("StreamUrl") or cli_args.get("streamUrl")
        token_value = cli_args.get("TokenValue") or cli_args.get("tokenValue")

        if not session_id:
            raise KeyError("Missing or empty required field: SessionId/sessionId")
        if not stream_url:
            raise KeyError("Missing or empty required field: StreamUrl/streamUrl")
        if not token_value:
            raise KeyError("Missing or empty required field: TokenValue/tokenValue")

        # Validate URL format
        if not self._is_valid_websocket_url(stream_url):
            raise ValueError(
                "streamUrl must be a valid WebSocket URL (ws:// or wss://)"
            )

        # Create configuration (validation happens in SessionConfig.__post_init__)
        return SessionConfig(
            session_id=session_id,
            stream_url=stream_url,
            token_value=token_value,
            target=cli_args.get("target"),
            document_name=cli_args.get("documentName"),
            parameters=cli_args.get("parameters", {}),
        )

    def _extract_client_config(self, cli_args: Dict[str, Any]) -> ClientConfig:
        """Extract client configuration from CLI arguments.

        Args:
            cli_args: CLI arguments dictionary

        Returns:
            Client configuration

        Raises:
            ValueError: If session type is unsupported
        """
        session_type_str = cli_args.get("sessionType", "Standard_Stream")

        try:
            session_type = SessionType(session_type_str)
        except ValueError as e:
            supported_types = [t.value for t in SessionType]
            raise ValueError(
                f"Unsupported session type '{session_type_str}'. "
                f"Supported types: {supported_types}"
            ) from e

        client_id = cli_args.get("clientId")
        if not client_id:
            client_id = str(uuid.uuid4())
            self._logger.debug(f"Generated client ID: {client_id}")

        return ClientConfig(
            session_type=session_type,
            client_id=client_id,
            input_stream_message_handler=cli_args.get("inputHandler"),
            output_stream_message_handler=cli_args.get("outputHandler"),
        )

    def _is_valid_websocket_url(self, url: str) -> bool:
        """Validate WebSocket URL format.

        Args:
            url: URL to validate

        Returns:
            True if valid WebSocket URL, False otherwise
        """
        try:
            parsed = urlparse(url)
            return parsed.scheme in ("ws", "wss") and bool(parsed.netloc)
        except Exception:
            return False

    async def terminate_session(self, session_id: str) -> None:
        """Terminate a specific session.

        Args:
            session_id: ID of session to terminate

        Raises:
            KeyError: If session not found
        """
        if session_id not in self._active_sessions:
            raise KeyError(f"Session {session_id} not found")

        session = self._active_sessions[session_id]
        try:
            await session.terminate_session()
            del self._active_sessions[session_id]
            self._logger.info(f"Session {session_id} terminated successfully")
        except Exception as e:
            self._logger.error(f"Error terminating session {session_id}: {e}")
            # Remove from tracking even if termination failed
            del self._active_sessions[session_id]
            raise

    async def terminate_all_sessions(self) -> None:
        """Terminate all active sessions."""
        session_ids = list(self._active_sessions.keys())
        errors = []

        for session_id in session_ids:
            try:
                await self.terminate_session(session_id)
            except Exception as e:
                errors.append(f"Failed to terminate {session_id}: {e}")
                self._logger.error(f"Error terminating session {session_id}: {e}")

        if errors:
            raise RuntimeError(
                f"Failed to terminate some sessions: {'; '.join(errors)}"
            )

        self._logger.info("All sessions terminated successfully")

    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get information about active sessions.

        Returns:
            List of session property dictionaries
        """
        return [
            session.get_session_properties().to_dict()
            for session in self._active_sessions.values()
        ]

    def get_session_count(self) -> int:
        """Get count of active sessions.

        Returns:
            Number of active sessions
        """
        return len(self._active_sessions)

    def is_session_active(self, session_id: str) -> bool:
        """Check if a session is currently active.

        Args:
            session_id: ID of session to check

        Returns:
            True if session is active, False otherwise
        """
        return session_id in self._active_sessions

    def get_session(self, session_id: str) -> ISession | None:
        """Get a specific active session.

        Args:
            session_id: ID of session to retrieve

        Returns:
            Session instance or None if not found
        """
        return self._active_sessions.get(session_id)
