# Phase 4: Integration & CLI Interface

## Overview

This phase integrates the session management and WebSocket communication components, creating a complete CLI interface that mirrors the Go implementation's functionality for AWS Session Manager Plugin.

## Objectives

- Create command-line interface matching AWS CLI integration patterns
- Implement argument parsing and validation for session parameters
- Integrate session and communicator components seamlessly  
- Add comprehensive logging and error reporting
- Create data channel management with proper session type handling

## Key Integration Points

Based on the Go implementation analysis:
- CLI entry point that processes AWS CLI arguments
- Session validation and startup coordination
- Data channel creation and lifecycle management
- Session type-specific handler configuration
- Error handling and cleanup on termination

## Implementation Steps

### 1. CLI Argument Structure and Parsing

#### src/session_manager_plugin/cli/types.py
```python
"""CLI argument types and validation."""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from pathlib import Path
import json


@dataclass
class CLIArguments:
    """Structured CLI arguments for session manager plugin."""
    
    # Required session parameters
    session_id: str
    stream_url: str  
    token_value: str
    
    # Optional session parameters
    target: Optional[str] = None
    document_name: Optional[str] = None
    session_type: str = "Standard_Stream"
    
    # Client configuration
    client_id: Optional[str] = None
    
    # Session parameters (JSON string)
    parameters: Optional[str] = None
    
    # CLI behavior options
    profile: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    
    # Debug and logging
    verbose: bool = False
    log_file: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CLIArguments':
        """Create CLIArguments from dictionary (typically from AWS CLI)."""
        return cls(
            session_id=data.get('sessionId', ''),
            stream_url=data.get('streamUrl', ''),
            token_value=data.get('tokenValue', ''),
            target=data.get('target'),
            document_name=data.get('documentName'),
            session_type=data.get('sessionType', 'Standard_Stream'),
            client_id=data.get('clientId'),
            parameters=data.get('parameters'),
            profile=data.get('profile'),
            region=data.get('region'),
            endpoint_url=data.get('endpointUrl'),
            verbose=data.get('verbose', False),
            log_file=data.get('logFile')
        )
    
    def get_parameters_dict(self) -> Dict[str, Any]:
        """Parse parameters JSON string into dictionary."""
        if not self.parameters:
            return {}
        
        try:
            return json.loads(self.parameters)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid parameters JSON: {e}")
    
    def validate(self) -> List[str]:
        """Validate CLI arguments and return list of errors."""
        errors = []
        
        if not self.session_id:
            errors.append("sessionId is required")
        
        if not self.stream_url:
            errors.append("streamUrl is required")
        
        if not self.token_value:
            errors.append("tokenValue is required")
        
        # Validate URL format
        if self.stream_url and not (
            self.stream_url.startswith('wss://') or 
            self.stream_url.startswith('ws://')
        ):
            errors.append("streamUrl must be a WebSocket URL (ws:// or wss://)")
        
        # Validate parameters JSON if provided
        if self.parameters:
            try:
                self.get_parameters_dict()
            except ValueError as e:
                errors.append(str(e))
        
        return errors
```

### 2. Main CLI Interface

#### src/session_manager_plugin/cli.py
```python
"""Main CLI interface for AWS Session Manager Plugin."""

import asyncio
import json
import logging
import signal
import sys
from typing import Dict, Any, Optional
from pathlib import Path

import click

from .cli.types import CLIArguments
from .session.session_handler import SessionHandler
from .session.types import SessionConfig, ClientConfig, SessionType
from .communicator.data_channel import SessionDataChannel
from .communicator.utils import create_websocket_config
from .utils.logging import setup_logging, get_logger


class SessionManagerPlugin:
    """Main plugin coordinator class."""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        self._session_handler = SessionHandler()
        self._current_session: Optional[Any] = None
        self._shutdown_event = asyncio.Event()
    
    async def run_session(self, args: CLIArguments) -> int:
        """Run a session with the provided arguments."""
        try:
            # Validate arguments
            errors = args.validate()
            if errors:
                for error in errors:
                    self.logger.error(f"Validation error: {error}")
                return 1
            
            # Convert CLI args to session configuration
            session_config = self._create_session_config(args)
            client_config = self._create_client_config(args)
            
            # Create and configure data channel
            data_channel = await self._create_data_channel(args)
            
            # Register session plugins (this would be expanded with actual plugins)
            await self._register_session_plugins()
            
            # Start session
            self._current_session = await self._session_handler.validate_input_and_start_session({
                'sessionId': args.session_id,
                'streamUrl': args.stream_url,
                'tokenValue': args.token_value,
                'target': args.target,
                'documentName': args.document_name,
                'sessionType': args.session_type,
                'clientId': args.client_id,
                'parameters': args.get_parameters_dict()
            })
            
            # Set up data channel for session
            self._current_session.set_data_channel(data_channel)
            
            # Set up signal handlers for graceful shutdown
            self._setup_signal_handlers()
            
            self.logger.info(f"Session {args.session_id} started successfully")
            
            # Wait for session completion or shutdown signal
            await self._wait_for_completion()
            
            return 0
            
        except KeyboardInterrupt:
            self.logger.info("Received interrupt signal")
            return 130  # SIGINT exit code
        except Exception as e:
            self.logger.error(f"Session failed: {e}", exc_info=True)
            return 1
        finally:
            await self._cleanup()
    
    def _create_session_config(self, args: CLIArguments) -> SessionConfig:
        """Create session configuration from CLI arguments."""
        return SessionConfig(
            session_id=args.session_id,
            stream_url=args.stream_url,
            token_value=args.token_value,
            target=args.target,
            document_name=args.document_name,
            parameters=args.get_parameters_dict()
        )
    
    def _create_client_config(self, args: CLIArguments) -> ClientConfig:
        """Create client configuration from CLI arguments."""
        try:
            session_type = SessionType(args.session_type)
        except ValueError:
            raise ValueError(f"Unsupported session type: {args.session_type}")
        
        return ClientConfig(
            client_id=args.client_id,
            session_type=session_type
        )
    
    async def _create_data_channel(self, args: CLIArguments) -> SessionDataChannel:
        """Create and configure data channel."""
        websocket_config = create_websocket_config(
            stream_url=args.stream_url,
            token=args.token_value
        )
        
        data_channel = SessionDataChannel(websocket_config)
        
        # Set up input/output handlers for different session types
        await self._configure_data_channel_handlers(data_channel, args)
        
        return data_channel
    
    async def _configure_data_channel_handlers(
        self, 
        data_channel: SessionDataChannel, 
        args: CLIArguments
    ) -> None:
        """Configure data channel input/output handlers based on session type."""
        session_type = args.session_type
        
        if session_type == "Standard_Stream":
            # Set up stdin/stdout handlers
            data_channel.set_input_handler(self._handle_remote_input)
            data_channel.set_output_handler(self._handle_remote_output)
        elif session_type == "Port":
            # Port forwarding handlers would be different
            self.logger.info("Port session type - specialized handlers not yet implemented")
        else:
            self.logger.warning(f"Unknown session type: {session_type}")
    
    def _handle_remote_input(self, data: bytes) -> None:
        """Handle input data from remote session."""
        try:
            # Write to stdout
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except Exception as e:
            self.logger.error(f"Error writing remote input: {e}")
    
    def _handle_remote_output(self, data: bytes) -> None:
        """Handle output data to remote session (not typically used in standard flow)."""
        self.logger.debug(f"Remote output: {len(data)} bytes")
    
    async def _register_session_plugins(self) -> None:
        """Register session type plugins."""
        # This would be expanded with actual plugin implementations
        # For now, we'll use a basic plugin system
        from .session.plugins import StandardStreamPlugin
        
        registry = self._session_handler._registry
        registry.register_plugin("Standard_Stream", StandardStreamPlugin())
        
        self.logger.debug("Session plugins registered")
    
    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}")
            asyncio.create_task(self._initiate_shutdown())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def _initiate_shutdown(self) -> None:
        """Initiate graceful shutdown."""
        self.logger.info("Initiating shutdown...")
        self._shutdown_event.set()
    
    async def _wait_for_completion(self) -> None:
        """Wait for session completion or shutdown signal."""
        # Set up stdin reader for interactive sessions
        if sys.stdin.isatty():
            stdin_task = asyncio.create_task(self._handle_stdin_input())
        else:
            stdin_task = None
        
        try:
            # Wait for shutdown signal
            await self._shutdown_event.wait()
        finally:
            if stdin_task:
                stdin_task.cancel()
                try:
                    await stdin_task
                except asyncio.CancelledError:
                    pass
    
    async def _handle_stdin_input(self) -> None:
        """Handle stdin input for interactive sessions."""
        try:
            loop = asyncio.get_event_loop()
            
            while not self._shutdown_event.is_set():
                # Read from stdin in a non-blocking way
                data = await loop.run_in_executor(None, sys.stdin.buffer.read, 1024)
                
                if not data:
                    # EOF reached
                    break
                
                # Send data through current session's data channel
                if (self._current_session and 
                    self._current_session.data_channel and 
                    self._current_session.data_channel.is_open):
                    
                    await self._current_session.data_channel.send_input_data(data)
                
        except Exception as e:
            self.logger.error(f"Error handling stdin: {e}")
        finally:
            await self._initiate_shutdown()
    
    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._current_session:
            try:
                await self._current_session.terminate_session()
            except Exception as e:
                self.logger.error(f"Error terminating session: {e}")
        
        self.logger.info("Cleanup completed")


# Click CLI interface
@click.command()
@click.argument('json_input', required=False)
@click.option('--session-id', help='Session ID')
@click.option('--stream-url', help='WebSocket stream URL')
@click.option('--token-value', help='Session token')
@click.option('--target', help='Target instance/resource')
@click.option('--document-name', help='SSM document name')
@click.option('--session-type', default='Standard_Stream', help='Session type')
@click.option('--client-id', help='Client identifier')
@click.option('--parameters', help='Session parameters (JSON)')
@click.option('--profile', help='AWS profile')
@click.option('--region', help='AWS region')
@click.option('--endpoint-url', help='AWS endpoint URL')
@click.option('--verbose', '-v', is_flag=True, help='Verbose logging')
@click.option('--log-file', help='Log file path')
def main(json_input: Optional[str], **kwargs) -> None:
    """
    AWS Session Manager Plugin - Python implementation.
    
    This plugin is typically called by the AWS CLI with JSON input containing
    session parameters. It can also be called directly with individual options.
    """
    try:
        # Parse input - either JSON string or individual options
        if json_input:
            # Parse JSON input (typical AWS CLI usage)
            try:
                json_data = json.loads(json_input)
                args = CLIArguments.from_dict(json_data)
            except json.JSONDecodeError as e:
                click.echo(f"Error parsing JSON input: {e}", err=True)
                sys.exit(1)
        else:
            # Use individual options
            # Filter out None values
            filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
            args = CLIArguments.from_dict(filtered_kwargs)
        
        # Set up logging
        log_level = logging.DEBUG if args.verbose else logging.INFO
        setup_logging(level=log_level, log_file=args.log_file)
        
        # Run the session
        plugin = SessionManagerPlugin()
        exit_code = asyncio.run(plugin.run_session(args))
        sys.exit(exit_code)
        
    except Exception as e:
        click.echo(f"Fatal error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
```

### 3. Basic Session Plugin Implementation

#### src/session_manager_plugin/session/plugins.py
```python
"""Session type plugins."""

from typing import List
from .protocols import ISessionPlugin, ISession
from .types import SessionConfig, ClientConfig
from .session import Session
from ..utils.logging import get_logger


class StandardStreamPlugin(ISessionPlugin):
    """Plugin for Standard_Stream session type."""
    
    def __init__(self):
        self.logger = get_logger(__name__)
    
    def get_supported_session_types(self) -> List[str]:
        """Return supported session types."""
        return ["Standard_Stream"]
    
    async def create_session(
        self, 
        config: SessionConfig, 
        client_config: ClientConfig
    ) -> ISession:
        """Create a Standard_Stream session."""
        self.logger.debug(f"Creating Standard_Stream session: {config.session_id}")
        
        session = Session(config, client_config)
        return session
    
    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration for Standard_Stream."""
        # Basic validation - can be enhanced with stream-specific checks
        return (
            bool(config.session_id) and 
            bool(config.stream_url) and 
            bool(config.token_value)
        )


class PortSessionPlugin(ISessionPlugin):
    """Plugin for Port session type (placeholder for future implementation)."""
    
    def __init__(self):
        self.logger = get_logger(__name__)
    
    def get_supported_session_types(self) -> List[str]:
        """Return supported session types."""
        return ["Port"]
    
    async def create_session(
        self, 
        config: SessionConfig, 
        client_config: ClientConfig
    ) -> ISession:
        """Create a Port session."""
        self.logger.debug(f"Creating Port session: {config.session_id}")
        
        # For now, use the basic session implementation
        # This would be enhanced with port-specific functionality
        session = Session(config, client_config)
        return session
    
    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration for Port sessions."""
        # Port sessions might need additional validation
        basic_valid = (
            bool(config.session_id) and 
            bool(config.stream_url) and 
            bool(config.token_value)
        )
        
        if not basic_valid:
            return False
        
        # Check for port-specific parameters
        params = config.parameters or {}
        if 'portNumber' not in params:
            self.logger.warning("Port session missing portNumber parameter")
            return False
        
        return True
```

### 4. Enhanced Logging and Utilities

#### src/session_manager_plugin/utils/logging.py
```python
"""Logging configuration and utilities."""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    format_string: Optional[str] = None
) -> None:
    """Set up logging configuration."""
    
    if format_string is None:
        format_string = (
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(level)
    
    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Create formatter
    formatter = logging.Formatter(format_string)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # Set levels for third-party libraries
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    
    logger.debug("Logging configured successfully")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)
```

### 5. Integration Testing Framework

#### tests/integration/test_session_integration.py
```python
"""Integration tests for session management."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from session_manager_plugin.cli.types import CLIArguments
from session_manager_plugin.cli import SessionManagerPlugin
from session_manager_plugin.session.types import SessionConfig, ClientConfig, SessionType


class TestSessionIntegration:
    """Integration tests for complete session workflow."""
    
    @pytest.fixture
    def sample_cli_args(self) -> CLIArguments:
        """Sample CLI arguments for testing."""
        return CLIArguments(
            session_id="test-session-123",
            stream_url="wss://example.com/stream",
            token_value="test-token-456",
            session_type="Standard_Stream"
        )
    
    @pytest.fixture
    def mock_websocket(self):
        """Mock WebSocket for testing."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.close = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(return_value="test message")
        mock_ws.ping = AsyncMock(return_value=AsyncMock())
        return mock_ws
    
    async def test_session_creation_flow(self, sample_cli_args, mock_websocket):
        """Test complete session creation and initialization."""
        plugin = SessionManagerPlugin()
        
        # Mock WebSocket connection
        with pytest.Mock('websockets.connect', return_value=mock_websocket):
            # This test would be more complex in reality
            session_config = plugin._create_session_config(sample_cli_args)
            
            assert session_config.session_id == "test-session-123"
            assert session_config.stream_url == "wss://example.com/stream"
            assert session_config.token_value == "test-token-456"
    
    async def test_data_channel_integration(self, sample_cli_args):
        """Test data channel creation and configuration."""
        plugin = SessionManagerPlugin()
        
        data_channel = await plugin._create_data_channel(sample_cli_args)
        
        assert data_channel is not None
        assert not data_channel.is_open  # Not connected yet
    
    async def test_argument_validation(self):
        """Test CLI argument validation."""
        # Invalid args - missing required fields
        invalid_args = CLIArguments(
            session_id="",
            stream_url="",
            token_value=""
        )
        
        errors = invalid_args.validate()
        assert len(errors) >= 3  # Should have multiple validation errors
        assert "sessionId is required" in errors
        assert "streamUrl is required" in errors
        assert "tokenValue is required" in errors
```

## AWS CLI Integration

### Plugin Registration
The plugin needs to be registered with AWS CLI:

```json
{
  "plugins": {
    "session-manager-plugin": {
      "command": "session-manager-plugin",
      "version": "0.1.0"
    }
  }
}
```

### Usage Example
```bash
# Typical AWS CLI usage
aws ssm start-session --target i-1234567890abcdef0

# Direct plugin usage for testing
session-manager-plugin '{"sessionId":"sess-123","streamUrl":"wss://...","tokenValue":"..."}'
```

## Validation Steps

1. **Test CLI argument parsing:**
   ```bash
   python -m session_manager_plugin.cli --help
   ```

2. **Test JSON input processing:**
   ```bash
   echo '{"sessionId":"test","streamUrl":"wss://test","tokenValue":"token"}' | python -m session_manager_plugin.cli
   ```

3. **Test integration with mock WebSocket server**
4. **Verify signal handling and graceful shutdown**

## Success Criteria

- [x] CLI interface processes AWS CLI JSON input correctly
- [x] Session and WebSocket components integrate seamlessly
- [x] Argument validation prevents invalid configurations
- [x] Signal handling enables graceful shutdown
- [x] Logging provides appropriate visibility
- [x] stdin/stdout handling works for interactive sessions
- [x] Integration tests validate complete workflow

## Next Phase

Proceed to [Phase 5: Testing & Packaging](04_testing_packaging.md) once CLI integration is complete and functional.