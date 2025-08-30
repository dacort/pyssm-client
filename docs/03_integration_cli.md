# Phase 4: Integration & CLI Interface

## Overview

This phase integrates the session management and WebSocket communication components, creating a complete CLI interface. Due to the complexity, this is split into two sub-phases:

**Phase 4A**: Core CLI structure with `connect` command (direct session parameters)
**Phase 4B**: `ssh` command with AWS SSM integration (user-friendly target-based sessions)

## Phase 4A Objectives

- Create Click-based CLI with subcommand structure
- Implement `connect` command for direct session parameters (AWS CLI integration)
- Integrate session and communicator components seamlessly  
- Add comprehensive logging and error reporting
- Create data channel management with proper session type handling

## Phase 4B Objectives (Follow-up)

- Add `ssh` command with AWS SSM API integration
- Implement target-based session creation using `ssm.start_session()`
- Add AWS credential discovery and configuration
- Provide SSM document defaults for common session types

## Key Integration Points

Based on the Go implementation analysis:
- CLI entry point that processes AWS CLI arguments
- Session validation and startup coordination
- Data channel creation and lifecycle management
- Session type-specific handler configuration
- Error handling and cleanup on termination

## Phase 4A Implementation Steps

### 1. CLI Argument Structure and Parsing

#### src/session_manager_plugin/cli/types.py
```python
"""CLI argument types and validation."""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from pathlib import Path
import json


@dataclass
class ConnectArguments:
    """CLI arguments for connect command (direct session parameters)."""
    
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

### 2. Main CLI Interface with Subcommands

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

from .cli.types import ConnectArguments
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
    
    async def run_session(self, args: ConnectArguments) -> int:
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
    
    def _create_session_config(self, args: ConnectArguments) -> SessionConfig:
        """Create session configuration from CLI arguments."""
        return SessionConfig(
            session_id=args.session_id,
            stream_url=args.stream_url,
            token_value=args.token_value,
            target=args.target,
            document_name=args.document_name,
            parameters=args.get_parameters_dict()
        )
    
    def _create_client_config(self, args: ConnectArguments) -> ClientConfig:
        """Create client configuration from CLI arguments."""
        try:
            session_type = SessionType(args.session_type)
        except ValueError:
            raise ValueError(f"Unsupported session type: {args.session_type}")
        
        return ClientConfig(
            client_id=args.client_id,
            session_type=session_type
        )
    
    async def _create_data_channel(self, args: ConnectArguments) -> SessionDataChannel:
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
        args: ConnectArguments
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


# Click CLI interface with subcommands
@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Verbose logging')
@click.option('--log-file', help='Log file path')
@click.pass_context
def cli(ctx, verbose: bool, log_file: Optional[str]) -> None:
    """AWS Session Manager Plugin - Python implementation."""
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['log_file'] = log_file
    
    # Set up logging
    log_level = logging.DEBUG if verbose else logging.INFO
    setup_logging(level=log_level, log_file=log_file)


@cli.command()
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
@click.pass_context
def connect(ctx, json_input: Optional[str], **kwargs) -> None:
    """
    Connect to existing session with direct parameters.
    
    This command is typically called by the AWS CLI with JSON input containing
    session parameters. It can also be called directly with individual options.
    """
    try:
        # Parse input - either JSON string or individual options
        if json_input:
            # Parse JSON input (typical AWS CLI usage)
            try:
                json_data = json.loads(json_input)
                args = ConnectArguments.from_dict(json_data)
            except json.JSONDecodeError as e:
                click.echo(f"Error parsing JSON input: {e}", err=True)
                sys.exit(1)
        else:
            # Use individual options
            # Filter out None values
            filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
            args = ConnectArguments.from_dict(filtered_kwargs)
        
        # Run the session
        plugin = SessionManagerPlugin()
        exit_code = asyncio.run(plugin.run_session(args))
        sys.exit(exit_code)
        
    except Exception as e:
        click.echo(f"Fatal error: {e}", err=True)
        sys.exit(1)


def main() -> int:
    """Main entry point."""
    cli()
    return 0


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

from session_manager_plugin.cli.types import ConnectArguments
from session_manager_plugin.cli import SessionManagerPlugin
from session_manager_plugin.session.types import SessionConfig, ClientConfig, SessionType


class TestSessionIntegration:
    """Integration tests for complete session workflow."""
    
    @pytest.fixture
    def sample_cli_args(self) -> ConnectArguments:
        """Sample CLI arguments for testing."""
        return ConnectArguments(
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
        invalid_args = ConnectArguments(
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

### Phase 4A Usage Examples
```bash
# AWS CLI integration (connects with existing session parameters)
session-manager-plugin connect '{"sessionId":"sess-123","streamUrl":"wss://...","tokenValue":"..."}'

# Direct connect command for testing
session-manager-plugin connect --session-id sess-123 --stream-url wss://example.com --token-value token123
```

## Phase 4A Validation Steps

1. **Test CLI argument parsing:**
   ```bash
   session-manager-plugin --help
   session-manager-plugin connect --help
   ```

2. **Test JSON input processing:**
   ```bash
   session-manager-plugin connect '{"sessionId":"test","streamUrl":"wss://test","tokenValue":"token"}'
   ```

3. **Test integration with mock WebSocket server**
4. **Verify signal handling and graceful shutdown**

## Phase 4A Success Criteria

- [ ] CLI interface with subcommands structure
- [ ] `connect` command processes AWS CLI JSON input correctly
- [ ] Session and WebSocket components integrate seamlessly
- [ ] Argument validation prevents invalid configurations
- [ ] Signal handling enables graceful shutdown
- [ ] Logging provides appropriate visibility
- [ ] stdin/stdout handling works for interactive sessions
- [ ] Integration tests validate complete workflow

## Phase 4B: SSH Command & AWS Integration

### Additional CLI Arguments for SSH Command

#### src/session_manager_plugin/cli/types.py (additions)
```python
@dataclass
class SSHArguments:
    """CLI arguments for ssh command (AWS SSM integration)."""
    
    # Required target
    target: str
    
    # Optional session configuration
    document_name: str = "SSM-SessionManagerRunShell"
    session_type: str = "Standard_Stream"
    
    # AWS configuration
    profile: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    
    # Additional session parameters
    parameters: Optional[Dict[str, Any]] = None
    
    def validate(self) -> List[str]:
        """Validate SSH arguments."""
        errors = []
        
        if not self.target:
            errors.append("target is required")
        
        # Basic target format validation (instance-id, etc.)
        if self.target and not (
            self.target.startswith('i-') or  # EC2 instance
            self.target.startswith('mi-') or  # Managed instance
            self.target.startswith('ssm-')    # Custom target
        ):
            errors.append("target must be a valid instance ID or managed instance ID")
        
        return errors
```

### SSH Command Implementation

```python
@cli.command()
@click.option('--target', required=True, help='Target EC2 instance or managed instance ID')
@click.option('--document-name', default='SSM-SessionManagerRunShell', help='SSM document name')
@click.option('--parameters', help='Session parameters (JSON)')
@click.option('--profile', help='AWS profile')
@click.option('--region', help='AWS region')
@click.option('--endpoint-url', help='AWS endpoint URL')
@click.pass_context
def ssh(ctx, **kwargs) -> None:
    """
    Start an interactive SSH-like session with AWS SSM.
    
    This command uses AWS SSM APIs to create a new session and then
    connects to it automatically.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
        
        # Parse arguments
        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        ssh_args = SSHArguments(**filtered_kwargs)
        
        # Validate arguments
        errors = ssh_args.validate()
        if errors:
            for error in errors:
                click.echo(f"Validation error: {error}", err=True)
            sys.exit(1)
        
        # Set up AWS session
        session_kwargs = {}
        if ssh_args.profile:
            session_kwargs['profile_name'] = ssh_args.profile
        if ssh_args.region:
            session_kwargs['region_name'] = ssh_args.region
        
        session = boto3.Session(**session_kwargs)
        ssm = session.client('ssm', endpoint_url=ssh_args.endpoint_url)
        
        # Build start_session parameters
        params = {'Target': ssh_args.target}
        if ssh_args.document_name:
            params['DocumentName'] = ssh_args.document_name
        if ssh_args.parameters:
            params['Parameters'] = ssh_args.parameters
        
        # Start session via SSM API
        try:
            response = ssm.start_session(**params)
        except (BotoCoreError, ClientError) as e:
            click.echo(f"Failed to start SSM session: {e}", err=True)
            sys.exit(1)
        
        # Extract session details
        session_id = response['SessionId']
        token_value = response['TokenValue']
        stream_url = response['StreamUrl']
        
        click.echo(f"Started SSM session: {session_id}")
        
        # Convert to ConnectArguments and run session
        connect_args = ConnectArguments(
            session_id=session_id,
            stream_url=stream_url,
            token_value=token_value,
            target=ssh_args.target,
            document_name=ssh_args.document_name,
            session_type=ssh_args.session_type
        )
        
        # Run the session
        plugin = SessionManagerPlugin()
        exit_code = asyncio.run(plugin.run_session(connect_args))
        sys.exit(exit_code)
        
    except Exception as e:
        click.echo(f"Fatal error: {e}", err=True)
        sys.exit(1)
```

### Phase 4B Usage Examples
```bash
# User-friendly SSH-like interface
session-manager-plugin ssh --target i-1234567890abcdef0

# SSH with custom document
session-manager-plugin ssh --target i-1234567890abcdef0 --document-name SSM-SessionManagerRunShell

# SSH with AWS profile and region
session-manager-plugin ssh --target i-1234567890abcdef0 --profile production --region us-west-2
```

## Combined Success Criteria

**Phase 4A:**
- [ ] CLI interface with subcommands structure
- [ ] `connect` command for direct session parameters
- [ ] Session and WebSocket integration working
- [ ] Argument validation and error handling
- [ ] Signal handling and graceful shutdown
- [ ] Integration tests passing

**Phase 4B:**
- [ ] `ssh` command with AWS SSM integration
- [ ] AWS credential discovery working
- [ ] SSM document defaults implemented
- [ ] Target-based session creation functional
- [ ] End-to-end user workflow complete

## Next Phase

Proceed to [Phase 5: Testing & Packaging](04_testing_packaging.md) once both Phase 4A and 4B are complete and functional.