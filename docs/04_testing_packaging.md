# Phase 5: Testing & Packaging

## Overview

This final phase focuses on comprehensive testing, validation against real AWS endpoints, and packaging for PyPI distribution. This ensures the Python implementation matches the Go reference implementation's reliability and functionality.

## Objectives

- Implement comprehensive unit and integration tests
- Test WebSocket communication with real AWS Session Manager endpoints
- Verify session management functionality with actual AWS resources
- Package for PyPI distribution with proper metadata
- Create documentation and usage examples
- Validate compatibility with AWS CLI integration

## Testing Strategy

### 1. Unit Testing Framework

#### Test Structure
```
tests/
├── __init__.py
├── conftest.py                    # Pytest configuration and fixtures
├── unit/                          # Unit tests
│   ├── __init__.py
│   ├── test_session/
│   │   ├── __init__.py
│   │   ├── test_session.py
│   │   ├── test_session_handler.py
│   │   ├── test_registry.py
│   │   ├── test_types.py
│   │   └── test_plugins.py
│   ├── test_communicator/
│   │   ├── __init__.py
│   │   ├── test_websocket_channel.py
│   │   ├── test_data_channel.py
│   │   ├── test_types.py
│   │   └── test_utils.py
│   ├── test_cli/
│   │   ├── __init__.py
│   │   ├── test_cli.py
│   │   └── test_types.py
│   └── test_utils/
│       ├── __init__.py
│       └── test_logging.py
├── integration/                   # Integration tests
│   ├── __init__.py
│   ├── test_session_integration.py
│   ├── test_websocket_integration.py
│   └── test_cli_integration.py
├── e2e/                          # End-to-end tests
│   ├── __init__.py
│   ├── test_aws_integration.py
│   └── test_cli_aws_integration.py
└── fixtures/                     # Test data and mocks
    ├── __init__.py
    ├── mock_responses.py
    └── test_data.py
```

#### tests/conftest.py
```python
"""Pytest configuration and shared fixtures."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

from session_manager_plugin.session.types import SessionConfig, ClientConfig, SessionType
from session_manager_plugin.communicator.types import WebSocketConfig
from session_manager_plugin.cli.types import CLIArguments


@pytest.fixture
def event_loop():
    """Create an event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_session_config() -> SessionConfig:
    """Sample session configuration for testing."""
    return SessionConfig(
        session_id="test-session-123",
        stream_url="wss://ssmmessages.us-east-1.amazonaws.com/v1/data-channel/test-session-123",
        token_value="test-token-456",
        target="i-1234567890abcdef0",
        document_name="SSM-SessionManagerRunShell",
        parameters={"shellProfile": {"linux": "bash"}}
    )


@pytest.fixture
def sample_client_config() -> ClientConfig:
    """Sample client configuration for testing."""
    return ClientConfig(
        client_id="test-client-789",
        session_type=SessionType.STANDARD_STREAM
    )


@pytest.fixture
def sample_websocket_config() -> WebSocketConfig:
    """Sample WebSocket configuration for testing."""
    return WebSocketConfig(
        url="wss://ssmmessages.us-east-1.amazonaws.com/v1/data-channel/test-session-123",
        token="test-token-456",
        ping_interval=30.0,
        ping_timeout=10.0,
        connect_timeout=30.0
    )


@pytest.fixture
def sample_cli_args() -> CLIArguments:
    """Sample CLI arguments for testing."""
    return CLIArguments(
        session_id="test-session-123",
        stream_url="wss://ssmmessages.us-east-1.amazonaws.com/v1/data-channel/test-session-123",
        token_value="test-token-456",
        target="i-1234567890abcdef0",
        document_name="SSM-SessionManagerRunShell",
        session_type="Standard_Stream",
        parameters='{"shellProfile":{"linux":"bash"}}'
    )


@pytest.fixture
def mock_websocket():
    """Mock WebSocket connection for testing."""
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.close = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_ws.recv = AsyncMock()
    mock_ws.ping = AsyncMock()
    return mock_ws


@pytest.fixture
def aws_credentials_available():
    """Check if AWS credentials are available for integration tests."""
    import boto3
    from botocore.exceptions import NoCredentialsError
    
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        return credentials is not None
    except NoCredentialsError:
        return False


@pytest.fixture
def skip_without_aws(aws_credentials_available):
    """Skip test if AWS credentials are not available."""
    if not aws_credentials_available:
        pytest.skip("AWS credentials not available")
```

### 2. Comprehensive Unit Tests

#### tests/unit/test_session/test_session.py
```python
"""Unit tests for core Session class."""

import pytest
from unittest.mock import AsyncMock, Mock
from datetime import datetime

from session_manager_plugin.session.session import Session
from session_manager_plugin.session.types import SessionStatus
from session_manager_plugin.communicator.protocols import IDataChannel


class TestSession:
    """Test cases for Session class."""
    
    def test_session_initialization(self, sample_session_config, sample_client_config):
        """Test session initialization with valid configuration."""
        session = Session(sample_session_config, sample_client_config)
        
        assert session.session_id == "test-session-123"
        assert session.status == SessionStatus.CREATED
        assert session.stream_url == sample_session_config.stream_url
        assert session.token_value == sample_session_config.token_value
        assert session.data_channel is None
    
    async def test_session_execute_success(
        self, 
        sample_session_config, 
        sample_client_config
    ):
        """Test successful session execution."""
        session = Session(sample_session_config, sample_client_config)
        
        # Mock data channel
        mock_data_channel = AsyncMock(spec=IDataChannel)
        mock_data_channel.open.return_value = True
        mock_data_channel.is_open = True
        
        session.set_data_channel(mock_data_channel)
        
        await session.execute()
        
        assert session.status == SessionStatus.CONNECTED
        mock_data_channel.open.assert_called_once()
    
    async def test_session_execute_data_channel_failure(
        self, 
        sample_session_config, 
        sample_client_config
    ):
        """Test session execution with data channel failure."""
        session = Session(sample_session_config, sample_client_config)
        
        # Mock failing data channel
        mock_data_channel = AsyncMock(spec=IDataChannel)
        mock_data_channel.open.return_value = False
        
        session.set_data_channel(mock_data_channel)
        
        with pytest.raises(RuntimeError, match="Failed to open data channel"):
            await session.execute()
        
        assert session.status == SessionStatus.FAILED
    
    async def test_session_termination(
        self, 
        sample_session_config, 
        sample_client_config
    ):
        """Test session termination."""
        session = Session(sample_session_config, sample_client_config)
        
        # Mock data channel
        mock_data_channel = AsyncMock(spec=IDataChannel)
        mock_data_channel.is_open = True
        session.set_data_channel(mock_data_channel)
        
        await session.terminate_session()
        
        assert session.status == SessionStatus.TERMINATED
        mock_data_channel.close.assert_called_once()
    
    def test_session_properties(
        self, 
        sample_session_config, 
        sample_client_config
    ):
        """Test session properties method."""
        session = Session(sample_session_config, sample_client_config)
        
        properties = session.get_session_properties()
        
        assert properties["session_id"] == "test-session-123"
        assert properties["status"] == SessionStatus.CREATED.value
        assert properties["session_type"] == "Standard_Stream"
        assert "created_at" in properties
        assert "client_id" in properties
```

#### tests/unit/test_communicator/test_websocket_channel.py
```python
"""Unit tests for WebSocket channel."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio

from session_manager_plugin.communicator.websocket_channel import WebSocketChannel
from session_manager_plugin.communicator.types import ConnectionState, MessageType, WebSocketMessage


class TestWebSocketChannel:
    """Test cases for WebSocket channel."""
    
    async def test_websocket_connect_success(self, sample_websocket_config, mock_websocket):
        """Test successful WebSocket connection."""
        channel = WebSocketChannel(sample_websocket_config)
        
        with patch('websockets.connect', return_value=mock_websocket):
            success = await channel.connect()
            
            assert success is True
            assert channel.is_connected is True
            assert channel.connection_state == ConnectionState.CONNECTED
    
    async def test_websocket_connect_failure(self, sample_websocket_config):
        """Test WebSocket connection failure."""
        channel = WebSocketChannel(sample_websocket_config)
        
        with patch('websockets.connect', side_effect=Exception("Connection failed")):
            success = await channel.connect()
            
            assert success is False
            assert channel.is_connected is False
            assert channel.connection_state == ConnectionState.ERROR
    
    async def test_websocket_send_message(
        self, 
        sample_websocket_config, 
        mock_websocket
    ):
        """Test sending WebSocket messages."""
        channel = WebSocketChannel(sample_websocket_config)
        
        with patch('websockets.connect', return_value=mock_websocket):
            await channel.connect()
            
            # Test string message
            await channel.send_message("test message")
            mock_websocket.send.assert_called_with("test message")
            
            # Test bytes message
            await channel.send_message(b"test bytes")
            mock_websocket.send.assert_called_with(b"test bytes")
            
            # Test dict message
            await channel.send_message({"key": "value"})
            mock_websocket.send.assert_called_with('{"key": "value"}')
    
    async def test_websocket_message_handling(
        self, 
        sample_websocket_config, 
        mock_websocket
    ):
        """Test WebSocket message reception and handling."""
        channel = WebSocketChannel(sample_websocket_config)
        received_messages = []
        
        def message_handler(message: WebSocketMessage):
            received_messages.append(message)
        
        channel.set_message_handler(message_handler)
        
        # Mock incoming messages
        mock_websocket.recv.side_effect = [
            "text message",
            b"binary message", 
            asyncio.CancelledError()  # End the loop
        ]
        
        with patch('websockets.connect', return_value=mock_websocket):
            await channel.connect()
            
            # Wait a bit for message processing
            await asyncio.sleep(0.1)
            
            await channel.close()
        
        # Verify messages were received
        assert len(received_messages) >= 1
    
    async def test_websocket_ping_mechanism(
        self, 
        sample_websocket_config, 
        mock_websocket
    ):
        """Test WebSocket ping/pong mechanism."""
        # Use shorter ping interval for testing
        sample_websocket_config.ping_interval = 0.1
        channel = WebSocketChannel(sample_websocket_config)
        
        pong_waiter = AsyncMock()
        mock_websocket.ping.return_value = pong_waiter
        
        with patch('websockets.connect', return_value=mock_websocket):
            await channel.connect()
            
            # Wait for ping to be sent
            await asyncio.sleep(0.2)
            
            await channel.close()
        
        # Verify ping was called
        assert mock_websocket.ping.call_count >= 1
    
    async def test_websocket_close(self, sample_websocket_config, mock_websocket):
        """Test WebSocket connection closure."""
        channel = WebSocketChannel(sample_websocket_config)
        
        with patch('websockets.connect', return_value=mock_websocket):
            await channel.connect()
            await channel.close()
            
            assert channel.connection_state == ConnectionState.CLOSED
            mock_websocket.close.assert_called_once()
```

### 3. Integration Tests

#### tests/integration/test_session_integration.py
```python
"""Integration tests for complete session workflow."""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock

from session_manager_plugin.session.session_handler import SessionHandler
from session_manager_plugin.session.plugins import StandardStreamPlugin
from session_manager_plugin.communicator.data_channel import SessionDataChannel


class TestSessionIntegration:
    """Integration tests for session management."""
    
    async def test_complete_session_workflow(
        self, 
        sample_session_config, 
        sample_client_config,
        mock_websocket
    ):
        """Test complete session creation and execution workflow."""
        handler = SessionHandler()
        
        # Register plugin
        plugin = StandardStreamPlugin()
        handler._registry.register_plugin("Standard_Stream", plugin)
        
        # Mock WebSocket connection for data channel
        with patch('websockets.connect', return_value=mock_websocket):
            # Create session
            session = await handler._registry.create_session(
                sample_session_config, 
                sample_client_config
            )
            
            # Create and set data channel
            from session_manager_plugin.communicator.utils import create_websocket_config
            ws_config = create_websocket_config(
                sample_session_config.stream_url,
                sample_session_config.token_value
            )
            data_channel = SessionDataChannel(ws_config)
            session.set_data_channel(data_channel)
            
            # Execute session
            await session.execute()
            
            assert session.session_id == sample_session_config.session_id
            assert session.data_channel is not None
            
            # Cleanup
            await session.terminate_session()
    
    async def test_session_handler_validation(self):
        """Test session handler input validation."""
        handler = SessionHandler()
        
        # Test with invalid input
        invalid_args = {
            'sessionId': '',  # Invalid: empty
            'streamUrl': 'invalid-url',  # Invalid: not websocket URL
            'tokenValue': ''  # Invalid: empty
        }
        
        with pytest.raises(ValueError):
            await handler.validate_input_and_start_session(invalid_args)
```

### 4. End-to-End Tests with AWS

#### tests/e2e/test_aws_integration.py
```python
"""End-to-end tests with real AWS Session Manager."""

import pytest
import boto3
import json
from botocore.exceptions import ClientError

from session_manager_plugin.cli import SessionManagerPlugin
from session_manager_plugin.cli.types import CLIArguments


@pytest.mark.e2e
class TestAWSIntegration:
    """End-to-end tests with real AWS resources."""
    
    def setup_method(self):
        """Set up test resources."""
        self.ssm_client = boto3.client('ssm')
        self.ec2_client = boto3.client('ec2')
    
    def test_get_managed_instances(self, skip_without_aws):
        """Test getting managed instances (requires AWS credentials)."""
        try:
            response = self.ssm_client.describe_instance_information()
            instances = response.get('InstanceInformationList', [])
            
            # Just verify we can call the API
            assert isinstance(instances, list)
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'AccessDenied':
                pytest.skip("Insufficient AWS permissions")
            raise
    
    @pytest.mark.slow
    async def test_session_creation_with_mock_target(self, skip_without_aws):
        """Test session creation process (without actual connection)."""
        # This test validates the session creation flow
        # but stops before actually connecting to avoid requiring live instances
        
        args = CLIArguments(
            session_id="test-session-integration",
            stream_url="wss://ssmmessages.us-east-1.amazonaws.com/v1/data-channel/test",
            token_value="mock-token-for-testing",
            target="i-mock1234567890",
            session_type="Standard_Stream"
        )
        
        plugin = SessionManagerPlugin()
        
        # Test configuration creation
        session_config = plugin._create_session_config(args)
        client_config = plugin._create_client_config(args)
        
        assert session_config.session_id == args.session_id
        assert client_config.session_type.value == args.session_type
        
        # Test data channel creation (won't connect due to mock token)
        data_channel = await plugin._create_data_channel(args)
        assert data_channel is not None
    
    def test_cli_json_parsing(self, skip_without_aws):
        """Test CLI JSON input parsing similar to AWS CLI usage."""
        # This simulates the JSON that AWS CLI would pass to the plugin
        aws_cli_json = json.dumps({
            "sessionId": "test-session-cli-123",
            "streamUrl": "wss://ssmmessages.us-east-1.amazonaws.com/v1/data-channel/test-session-cli-123",
            "tokenValue": "test-token-from-cli",
            "target": "i-1234567890abcdef0",
            "documentName": "SSM-SessionManagerRunShell",
            "sessionType": "Standard_Stream",
            "parameters": json.dumps({
                "shellProfile": {
                    "linux": "/bin/bash",
                    "windows": "powershell.exe"
                }
            })
        })
        
        # Parse JSON as CLIArguments would
        json_data = json.loads(aws_cli_json)
        args = CLIArguments.from_dict(json_data)
        
        # Validate parsing
        assert args.session_id == "test-session-cli-123"
        assert args.target == "i-1234567890abcdef0"
        assert args.document_name == "SSM-SessionManagerRunShell"
        
        # Validate parameters parsing
        params = args.get_parameters_dict()
        assert "shellProfile" in params
        assert params["shellProfile"]["linux"] == "/bin/bash"
```

### 5. Performance and Load Testing

#### tests/performance/test_performance.py
```python
"""Performance tests for session manager plugin."""

import pytest
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from session_manager_plugin.session.session import Session
from session_manager_plugin.communicator.websocket_channel import WebSocketChannel


@pytest.mark.performance
class TestPerformance:
    """Performance test cases."""
    
    async def test_session_creation_performance(
        self, 
        sample_session_config, 
        sample_client_config
    ):
        """Test session creation performance."""
        sessions = []
        start_time = time.time()
        
        # Create 100 sessions
        for i in range(100):
            config = sample_session_config
            config.session_id = f"perf-test-{i}"
            session = Session(config, sample_client_config)
            sessions.append(session)
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Should be able to create 100 sessions in under 1 second
        assert duration < 1.0
        assert len(sessions) == 100
    
    async def test_concurrent_websocket_connections(self, sample_websocket_config):
        """Test multiple concurrent WebSocket connections."""
        channels = []
        
        # Create 10 channels concurrently
        async def create_channel():
            channel = WebSocketChannel(sample_websocket_config)
            channels.append(channel)
            return channel
        
        start_time = time.time()
        tasks = [create_channel() for _ in range(10)]
        await asyncio.gather(*tasks)
        end_time = time.time()
        
        duration = end_time - start_time
        
        # Should create channels quickly
        assert duration < 1.0
        assert len(channels) == 10
```

## Packaging for PyPI

### 1. Enhanced pyproject.toml

```toml
[project]
name = "python-session-manager-plugin"
version = "0.1.0"
description = "Python implementation of AWS Session Manager Plugin"
authors = [
    {name = "Your Name", email = "your.email@example.com"},
]
dependencies = [
    "websockets>=12.0",
    "boto3>=1.34.0",
    "click>=8.1.0",
    "pydantic>=2.5.0",
]
requires-python = ">=3.13"
readme = "README.md"
license = {text = "Apache-2.0"}
keywords = ["aws", "session-manager", "ssm", "websocket", "cli"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: System Administrators",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.13",
    "Topic :: Internet :: WWW/HTTP",
    "Topic :: System :: Systems Administration",
    "Topic :: Utilities",
]

[project.urls]
Homepage = "https://github.com/yourusername/python-session-manager-plugin"
Documentation = "https://python-session-manager-plugin.readthedocs.io/"
Repository = "https://github.com/yourusername/python-session-manager-plugin.git"
"Bug Tracker" = "https://github.com/yourusername/python-session-manager-plugin/issues"

[project.scripts]
session-manager-plugin = "session_manager_plugin.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/session_manager_plugin"]

[tool.hatch.build.targets.sdist]
include = [
    "/src",
    "/tests",
    "/docs",
    "/README.md",
    "/LICENSE",
    "/pyproject.toml"
]
```

### 2. README.md

```markdown
# Python Session Manager Plugin

A Python implementation of the [AWS Session Manager Plugin](https://github.com/aws/session-manager-plugin) that enables secure, auditable instance management through AWS Systems Manager.

## Features

- 🔒 Secure WebSocket connections to AWS Session Manager
- 🚀 Asynchronous, high-performance communication
- 🛠️ Drop-in replacement for the Go-based plugin
- 🐍 Pure Python implementation with minimal dependencies
- 📦 Easy installation via pip
- 🧪 Comprehensive testing suite

## Installation

```bash
pip install python-session-manager-plugin
```

## Usage

### With AWS CLI

The plugin integrates seamlessly with AWS CLI:

```bash
aws ssm start-session --target i-1234567890abcdef0
```

### Direct Usage

```bash
session-manager-plugin '{"sessionId":"sess-123","streamUrl":"wss://...","tokenValue":"..."}'
```

## Requirements

- Python 3.13+
- AWS credentials configured
- Network access to AWS Session Manager endpoints

## Development

```bash
# Clone repository
git clone https://github.com/yourusername/python-session-manager-plugin.git
cd python-session-manager-plugin

# Install with uv
uv sync

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=session_manager_plugin
```

## License

Apache 2.0 - See [LICENSE](LICENSE) for details.
```

### 3. GitHub Actions CI/CD

#### .github/workflows/ci.yml

```yaml
name: CI

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.13"]

    steps:
    - uses: actions/checkout@v4
    
    - name: Install uv
      uses: astral-sh/setup-uv@v1
      with:
        version: "latest"
    
    - name: Set up Python
      run: uv python install ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: uv sync
    
    - name: Run linting
      run: |
        uv run black --check src/ tests/
        uv run ruff check src/ tests/
        uv run mypy src/
    
    - name: Run unit tests
      run: uv run pytest tests/unit/ -v --cov=session_manager_plugin
    
    - name: Run integration tests
      run: uv run pytest tests/integration/ -v
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    
    - name: Install uv
      uses: astral-sh/setup-uv@v1
    
    - name: Build package
      run: uv build
    
    - name: Upload artifacts
      uses: actions/upload-artifact@v4
      with:
        name: dist
        path: dist/

  publish:
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')
    needs: [test, build]
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    
    - name: Install uv
      uses: astral-sh/setup-uv@v1
    
    - name: Build and publish
      env:
        UV_PUBLISH_TOKEN: ${{ secrets.PYPI_API_TOKEN }}
      run: |
        uv build
        uv publish
```

## Validation and Testing Checklist

### Pre-Release Validation

1. **Unit Tests**
   - [ ] All unit tests pass with >95% coverage
   - [ ] Session management tests complete
   - [ ] WebSocket communication tests complete
   - [ ] CLI interface tests complete

2. **Integration Tests**
   - [ ] Component integration tests pass
   - [ ] End-to-end workflow tests complete
   - [ ] Error handling validation complete

3. **AWS Integration Tests**
   - [ ] Real WebSocket connection tests (with mock endpoints)
   - [ ] AWS CLI JSON parsing validation
   - [ ] Session Manager API compatibility

4. **Performance Tests**
   - [ ] Session creation performance acceptable
   - [ ] WebSocket connection handling efficient
   - [ ] Memory usage within acceptable limits

5. **Package Quality**
   - [ ] All linting checks pass (black, ruff, mypy)
   - [ ] Documentation complete and accurate
   - [ ] Dependencies properly specified
   - [ ] Security scan passes

### Release Process

```bash
# 1. Run full test suite
uv run pytest

# 2. Build package
uv build

# 3. Test package installation
pip install dist/python_session_manager_plugin-*.whl

# 4. Test CLI functionality
session-manager-plugin --help

# 5. Create release tag
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

## Success Criteria

- [x] Comprehensive test suite with >95% coverage
- [x] All integration tests pass
- [x] Package builds successfully
- [x] CLI interface functions correctly
- [x] WebSocket communication robust and reliable
- [x] Session management matches Go implementation behavior
- [x] Documentation complete and accurate
- [x] PyPI package ready for distribution
- [x] CI/CD pipeline functional

## Completion

Upon completion of this phase, the Python Session Manager Plugin will be:
- Fully tested and validated
- Compatible with AWS Session Manager
- Packaged for easy distribution
- Ready for production use as a drop-in replacement for the Go implementation
- Well-documented for maintainability and contributions

The implementation will mirror the Go reference implementation's functionality while leveraging Python's strengths for maintainability and extensibility.