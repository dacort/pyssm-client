# Phase 2: Core Session Management

## Overview

This phase implements the core session management functionality, mirroring the Go implementation's session handling capabilities with Python-native patterns.

## Objectives

- Create Session class with core properties and methods
- Define interfaces using Python protocols/ABC
- Implement session validation and lifecycle management
- Create session type registry system

## Key Components from Go Analysis

Based on the Go implementation analysis:
- `Session` struct with SessionId, StreamUrl, TokenValue, DataChannel
- `ISessionPlugin` and `ISession` interfaces
- Session validation and startup logic
- Session type-specific handlers

## Implementation Steps

### 1. Core Session Data Structures

#### src/session_manager_plugin/session/types.py
```python
"""Session data types and enums."""

from enum import Enum
from typing import Any, Dict, Optional
from dataclasses import dataclass
from datetime import datetime


class SessionType(Enum):
    """Supported session types."""
    STANDARD_STREAM = "Standard_Stream"
    PORT = "Port"
    INTERACTIVE_COMMANDS = "InteractiveCommands"


class SessionStatus(Enum):
    """Session status states."""
    CREATED = "Created"
    CONNECTED = "Connected"
    TERMINATING = "Terminating"
    TERMINATED = "Terminated"
    FAILED = "Failed"


@dataclass
class SessionConfig:
    """Configuration for session initialization."""
    session_id: str
    stream_url: str
    token_value: str
    target: Optional[str] = None
    document_name: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}


@dataclass
class ClientConfig:
    """Client configuration for session."""
    client_id: str
    session_type: SessionType
    input_stream_message_handler: Optional[Any] = None
    output_stream_message_handler: Optional[Any] = None
```

### 2. Session Interface Definitions

#### src/session_manager_plugin/session/protocols.py
```python
"""Session management protocols (interfaces)."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Protocol
from .types import SessionConfig, ClientConfig, SessionStatus


class IDataChannel(Protocol):
    """Interface for data channel operations."""
    
    async def open(self) -> bool:
        """Open the data channel connection."""
        ...
    
    async def send_input_data(self, data: bytes) -> None:
        """Send input data through the channel."""
        ...
    
    async def close(self) -> None:
        """Close the data channel."""
        ...
    
    @property
    def is_open(self) -> bool:
        """Check if channel is open."""
        ...


class ISession(Protocol):
    """Core session interface."""
    
    async def execute(self) -> None:
        """Execute the session."""
        ...
    
    async def open_data_channel(self) -> bool:
        """Open the data channel for this session."""
        ...
    
    async def terminate_session(self) -> None:
        """Terminate the session."""
        ...
    
    @property
    def session_id(self) -> str:
        """Get session identifier."""
        ...
    
    @property
    def status(self) -> SessionStatus:
        """Get current session status."""
        ...


class ISessionPlugin(ABC):
    """Abstract base class for session plugins."""
    
    @abstractmethod
    def get_supported_session_types(self) -> list[str]:
        """Return list of supported session types."""
        pass
    
    @abstractmethod
    async def create_session(
        self, 
        config: SessionConfig, 
        client_config: ClientConfig
    ) -> ISession:
        """Create a new session instance."""
        pass
    
    @abstractmethod
    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration."""
        pass
```

### 3. Core Session Implementation

#### src/session_manager_plugin/session/session.py
```python
"""Core session implementation."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from .types import SessionConfig, ClientConfig, SessionStatus, SessionType
from .protocols import ISession, IDataChannel
from ..utils.logging import get_logger


class Session(ISession):
    """Core session implementation."""
    
    def __init__(self, config: SessionConfig, client_config: ClientConfig):
        self.logger = get_logger(__name__)
        self._config = config
        self._client_config = client_config
        self._status = SessionStatus.CREATED
        self._data_channel: Optional[IDataChannel] = None
        self._created_at = datetime.now()
        self._client_id = client_config.client_id or str(uuid.uuid4())
        
    @property
    def session_id(self) -> str:
        """Get session identifier."""
        return self._config.session_id
    
    @property
    def status(self) -> SessionStatus:
        """Get current session status."""
        return self._status
    
    @property
    def stream_url(self) -> str:
        """Get WebSocket stream URL."""
        return self._config.stream_url
    
    @property
    def token_value(self) -> str:
        """Get session token."""
        return self._config.token_value
    
    @property
    def data_channel(self) -> Optional[IDataChannel]:
        """Get data channel instance."""
        return self._data_channel
    
    async def execute(self) -> None:
        """Execute the session by opening data channel and setting up handlers."""
        try:
            self.logger.info(f"Executing session {self.session_id}")
            
            # Open data channel
            if not await self.open_data_channel():
                raise RuntimeError("Failed to open data channel")
            
            self._status = SessionStatus.CONNECTED
            self.logger.info(f"Session {self.session_id} connected successfully")
            
            # Set up session-specific handlers based on session type
            await self._setup_session_handlers()
            
        except Exception as e:
            self.logger.error(f"Failed to execute session {self.session_id}: {e}")
            self._status = SessionStatus.FAILED
            raise
    
    async def open_data_channel(self) -> bool:
        """Open the data channel for this session."""
        try:
            if self._data_channel is None:
                # Data channel will be injected by session factory
                raise RuntimeError("Data channel not initialized")
            
            success = await self._data_channel.open()
            if success:
                self.logger.debug(f"Data channel opened for session {self.session_id}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Failed to open data channel: {e}")
            return False
    
    async def terminate_session(self) -> None:
        """Terminate the session and cleanup resources."""
        try:
            self.logger.info(f"Terminating session {self.session_id}")
            self._status = SessionStatus.TERMINATING
            
            # Close data channel
            if self._data_channel and self._data_channel.is_open:
                await self._data_channel.close()
            
            self._status = SessionStatus.TERMINATED
            self.logger.info(f"Session {self.session_id} terminated successfully")
            
        except Exception as e:
            self.logger.error(f"Error during session termination: {e}")
            self._status = SessionStatus.FAILED
            raise
    
    async def _setup_session_handlers(self) -> None:
        """Set up session-specific message handlers."""
        session_type = self._client_config.session_type
        self.logger.debug(f"Setting up handlers for session type: {session_type}")
        
        # Implementation will be session-type specific
        # This will be expanded in later phases
        pass
    
    def set_data_channel(self, data_channel: IDataChannel) -> None:
        """Set the data channel for this session."""
        self._data_channel = data_channel
    
    def get_session_properties(self) -> Dict[str, Any]:
        """Get session properties for debugging/monitoring."""
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "session_type": self._client_config.session_type.value,
            "created_at": self._created_at.isoformat(),
            "client_id": self._client_id,
            "stream_url": self.stream_url,
            "has_data_channel": self._data_channel is not None,
            "data_channel_open": (
                self._data_channel.is_open if self._data_channel else False
            ),
        }
```

### 4. Session Registry and Factory

#### src/session_manager_plugin/session/registry.py
```python
"""Session plugin registry."""

import logging
from typing import Dict, List, Type, Optional

from .protocols import ISessionPlugin, ISession
from .types import SessionConfig, ClientConfig, SessionType
from ..utils.logging import get_logger


class SessionPluginRegistry:
    """Registry for session plugins."""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        self._plugins: Dict[str, ISessionPlugin] = {}
    
    def register_plugin(self, session_type: str, plugin: ISessionPlugin) -> None:
        """Register a session plugin for a specific session type."""
        self.logger.debug(f"Registering plugin for session type: {session_type}")
        self._plugins[session_type] = plugin
    
    def get_plugin(self, session_type: str) -> Optional[ISessionPlugin]:
        """Get plugin for a specific session type."""
        return self._plugins.get(session_type)
    
    def get_supported_session_types(self) -> List[str]:
        """Get all supported session types."""
        return list(self._plugins.keys())
    
    async def create_session(
        self, 
        config: SessionConfig, 
        client_config: ClientConfig
    ) -> ISession:
        """Create a session using the appropriate plugin."""
        session_type = client_config.session_type.value
        
        plugin = self.get_plugin(session_type)
        if not plugin:
            raise ValueError(f"No plugin registered for session type: {session_type}")
        
        if not plugin.validate_session_properties(config):
            raise ValueError(f"Invalid session configuration for type: {session_type}")
        
        return await plugin.create_session(config, client_config)


# Global registry instance
_registry = SessionPluginRegistry()


def get_session_registry() -> SessionPluginRegistry:
    """Get the global session registry."""
    return _registry
```

### 5. Session Handler and Validation

#### src/session_manager_plugin/session/session_handler.py
```python
"""Session handler for managing session lifecycle."""

import asyncio
import logging
import uuid
from typing import Dict, Any, Optional, List

from .session import Session
from .registry import get_session_registry
from .types import SessionConfig, ClientConfig, SessionType
from ..utils.logging import get_logger


class SessionHandler:
    """Handles session creation, management, and lifecycle."""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        self._registry = get_session_registry()
        self._active_sessions: Dict[str, Session] = {}
    
    async def validate_input_and_start_session(
        self, 
        cli_args: Dict[str, Any]
    ) -> Session:
        """Validate CLI input and start a new session."""
        try:
            # Extract and validate required parameters
            config = self._extract_session_config(cli_args)
            client_config = self._extract_client_config(cli_args)
            
            # Validate configuration
            self._validate_session_config(config)
            
            # Create session using registry
            session = await self._registry.create_session(config, client_config)
            
            # Track active session
            self._active_sessions[config.session_id] = session
            
            # Start session
            await session.execute()
            
            self.logger.info(f"Session {config.session_id} started successfully")
            return session
            
        except Exception as e:
            self.logger.error(f"Failed to start session: {e}")
            raise
    
    def _extract_session_config(self, cli_args: Dict[str, Any]) -> SessionConfig:
        """Extract session configuration from CLI arguments."""
        required_fields = ['sessionId', 'streamUrl', 'tokenValue']
        
        for field in required_fields:
            if field not in cli_args:
                raise ValueError(f"Missing required field: {field}")
        
        return SessionConfig(
            session_id=cli_args['sessionId'],
            stream_url=cli_args['streamUrl'],
            token_value=cli_args['tokenValue'],
            target=cli_args.get('target'),
            document_name=cli_args.get('documentName'),
            parameters=cli_args.get('parameters', {})
        )
    
    def _extract_client_config(self, cli_args: Dict[str, Any]) -> ClientConfig:
        """Extract client configuration from CLI arguments."""
        session_type_str = cli_args.get('sessionType', 'Standard_Stream')
        
        try:
            session_type = SessionType(session_type_str)
        except ValueError:
            raise ValueError(f"Unsupported session type: {session_type_str}")
        
        return ClientConfig(
            client_id=cli_args.get('clientId', str(uuid.uuid4())),
            session_type=session_type
        )
    
    def _validate_session_config(self, config: SessionConfig) -> None:
        """Validate session configuration."""
        if not config.session_id:
            raise ValueError("Session ID cannot be empty")
        
        if not config.stream_url:
            raise ValueError("Stream URL cannot be empty")
        
        if not config.token_value:
            raise ValueError("Token value cannot be empty")
        
        # Validate URL format
        if not config.stream_url.startswith(('ws://', 'wss://')):
            raise ValueError("Stream URL must be a WebSocket URL (ws:// or wss://)")
    
    async def terminate_session(self, session_id: str) -> None:
        """Terminate a specific session."""
        session = self._active_sessions.get(session_id)
        if not session:
            self.logger.warning(f"Session {session_id} not found")
            return
        
        try:
            await session.terminate_session()
            del self._active_sessions[session_id]
            self.logger.info(f"Session {session_id} terminated")
        except Exception as e:
            self.logger.error(f"Error terminating session {session_id}: {e}")
            raise
    
    async def terminate_all_sessions(self) -> None:
        """Terminate all active sessions."""
        session_ids = list(self._active_sessions.keys())
        
        for session_id in session_ids:
            try:
                await self.terminate_session(session_id)
            except Exception as e:
                self.logger.error(f"Error terminating session {session_id}: {e}")
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get information about active sessions."""
        return [
            session.get_session_properties() 
            for session in self._active_sessions.values()
        ]
```

## Testing Requirements

### Unit Tests Structure
```
tests/test_session/
├── test_session.py
├── test_session_handler.py
├── test_registry.py
└── test_types.py
```

### Key Test Cases
1. Session creation and initialization
2. Session lifecycle (execute, terminate)
3. Configuration validation
4. Registry plugin management
5. Error handling and status management

## Validation Steps

1. **Create test session configuration:**
   ```python
   config = SessionConfig(
       session_id="test-session",
       stream_url="wss://example.com/stream",
       token_value="test-token"
   )
   ```

2. **Test session creation and basic operations**
3. **Verify registry functionality**
4. **Test error handling and validation**

## Success Criteria

- [x] Session class implements ISession protocol
- [x] Session configuration validation works
- [x] Registry can manage session plugins
- [x] Session lifecycle methods work correctly
- [x] Comprehensive error handling implemented
- [x] Unit tests pass with >90% coverage

## Next Phase

Proceed to [Phase 3: WebSocket Communication](02_websocket_communication.md) once session management is fully implemented and tested.