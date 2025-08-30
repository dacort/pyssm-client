# Phase 3: WebSocket Communication Layer

## Overview

This phase implements the WebSocket communication layer based on the Go implementation's `communicator/websocketchannel.go`, providing robust, asynchronous communication with AWS Session Manager.

## Objectives

- Implement WebSocket channel using `websockets` library
- Create connection lifecycle management with health checks
- Add message handling for text/binary data with proper framing
- Implement thread-safe operations using asyncio
- Add comprehensive error handling and retry mechanisms

## Key Components from Go Analysis

Based on the Go WebSocket implementation:
- Connection initialization with URL and token
- Ping/pong mechanism for connection health
- Concurrent message listening with goroutines
- Mutex-protected message writing
- Configurable error and message callbacks
- Binary and text message support

## Implementation Steps

### 1. WebSocket Channel Interface and Types

#### src/session_manager_plugin/communicator/types.py
```python
"""WebSocket communication types and enums."""

from enum import Enum
from typing import Any, Callable, Optional, Union
from dataclasses import dataclass


class MessageType(Enum):
    """WebSocket message types."""
    TEXT = "text"
    BINARY = "binary"
    CLOSE = "close"
    ERROR = "error"


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class WebSocketMessage:
    """WebSocket message container."""
    message_type: MessageType
    data: Union[str, bytes]
    timestamp: Optional[float] = None


@dataclass
class WebSocketConfig:
    """WebSocket connection configuration."""
    url: str
    token: str
    ping_interval: float = 30.0  # seconds
    ping_timeout: float = 10.0   # seconds
    connect_timeout: float = 30.0  # seconds
    max_message_size: int = 1024 * 1024  # 1MB
    max_queue_size: int = 100
    retry_attempts: int = 3
    retry_delay: float = 1.0  # seconds


# Type aliases for callbacks
MessageHandler = Callable[[WebSocketMessage], None]
ErrorHandler = Callable[[Exception], None]
ConnectionHandler = Callable[[ConnectionState], None]
```

### 2. Core WebSocket Channel Implementation

#### src/session_manager_plugin/communicator/websocket_channel.py
```python
"""WebSocket channel implementation for AWS Session Manager communication."""

import asyncio
import logging
import time
import json
from typing import Optional, Union, Dict, Any
from contextlib import asynccontextmanager

import websockets
from websockets.exceptions import (
    ConnectionClosed, 
    WebSocketException, 
    InvalidStatusCode,
    ConnectionClosedError
)

from .types import (
    WebSocketConfig, 
    WebSocketMessage, 
    MessageType, 
    ConnectionState,
    MessageHandler,
    ErrorHandler, 
    ConnectionHandler
)
from ..utils.logging import get_logger


class WebSocketChannel:
    """WebSocket channel for AWS Session Manager communication."""
    
    def __init__(self, config: WebSocketConfig):
        self.logger = get_logger(__name__)
        self._config = config
        self._websocket: Optional[websockets.WebSocketServerProtocol] = None
        self._connection_state = ConnectionState.DISCONNECTED
        
        # Async coordination
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._message_queue: asyncio.Queue[WebSocketMessage] = asyncio.Queue(
            maxsize=config.max_queue_size
        )
        
        # Background tasks
        self._ping_task: Optional[asyncio.Task] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._message_processor_task: Optional[asyncio.Task] = None
        
        # Event handlers
        self._message_handler: Optional[MessageHandler] = None
        self._error_handler: Optional[ErrorHandler] = None
        self._connection_handler: Optional[ConnectionHandler] = None
        
        # Metrics and monitoring
        self._last_ping_time: Optional[float] = None
        self._last_pong_time: Optional[float] = None
        self._message_count = 0
        self._error_count = 0
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return (
            self._connection_state == ConnectionState.CONNECTED 
            and self._websocket is not None 
            and not self._websocket.closed
        )
    
    @property
    def connection_state(self) -> ConnectionState:
        """Get current connection state."""
        return self._connection_state
    
    def set_message_handler(self, handler: MessageHandler) -> None:
        """Set message handler callback."""
        self._message_handler = handler
    
    def set_error_handler(self, handler: ErrorHandler) -> None:
        """Set error handler callback."""
        self._error_handler = handler
    
    def set_connection_handler(self, handler: ConnectionHandler) -> None:
        """Set connection state change handler."""
        self._connection_handler = handler
    
    async def connect(self) -> bool:
        """Establish WebSocket connection with retry logic."""
        async with self._connect_lock:
            if self.is_connected:
                self.logger.debug("Already connected")
                return True
            
            retry_count = 0
            while retry_count < self._config.retry_attempts:
                try:
                    await self._attempt_connection()
                    return True
                    
                except Exception as e:
                    retry_count += 1
                    self.logger.warning(
                        f"Connection attempt {retry_count} failed: {e}"
                    )
                    
                    if retry_count < self._config.retry_attempts:
                        await asyncio.sleep(self._config.retry_delay * retry_count)
                    else:
                        self.logger.error("All connection attempts failed")
                        await self._set_connection_state(ConnectionState.ERROR)
                        return False
            
            return False
    
    async def _attempt_connection(self) -> None:
        """Attempt a single WebSocket connection."""
        self.logger.info(f"Connecting to {self._config.url}")
        await self._set_connection_state(ConnectionState.CONNECTING)
        
        # Prepare connection headers
        headers = {
            "Authorization": f"Bearer {self._config.token}",
            "User-Agent": "python-session-manager-plugin/0.1.0"
        }
        
        try:
            # Establish WebSocket connection
            self._websocket = await asyncio.wait_for(
                websockets.connect(
                    self._config.url,
                    extra_headers=headers,
                    max_size=self._config.max_message_size,
                    ping_interval=None,  # We handle pings manually
                    ping_timeout=None
                ),
                timeout=self._config.connect_timeout
            )
            
            await self._set_connection_state(ConnectionState.CONNECTED)
            self.logger.info("WebSocket connection established")
            
            # Start background tasks
            await self._start_background_tasks()
            
        except asyncio.TimeoutError:
            raise Exception(f"Connection timeout after {self._config.connect_timeout}s")
        except InvalidStatusCode as e:
            raise Exception(f"HTTP {e.status_code}: {e}")
        except Exception as e:
            raise Exception(f"Connection failed: {e}")
    
    async def _start_background_tasks(self) -> None:
        """Start background tasks for ping and message handling."""
        # Start ping task
        self._ping_task = asyncio.create_task(self._ping_loop())
        
        # Start message listener
        self._listener_task = asyncio.create_task(self._message_listener())
        
        # Start message processor
        self._message_processor_task = asyncio.create_task(self._message_processor())
        
        self.logger.debug("Background tasks started")
    
    async def send_message(self, data: Union[str, bytes, dict]) -> None:
        """Send message through WebSocket with thread safety."""
        if not self.is_connected:
            raise RuntimeError("WebSocket not connected")
        
        async with self._send_lock:
            try:
                if isinstance(data, dict):
                    # Convert dict to JSON string
                    message_data = json.dumps(data)
                    await self._websocket.send(message_data)
                elif isinstance(data, str):
                    await self._websocket.send(data)
                elif isinstance(data, bytes):
                    await self._websocket.send(data)
                else:
                    raise ValueError(f"Unsupported message type: {type(data)}")
                
                self.logger.debug(f"Sent message: {len(str(data))} chars/bytes")
                
            except ConnectionClosed as e:
                self.logger.error(f"Connection closed while sending: {e}")
                await self._set_connection_state(ConnectionState.CLOSED)
                raise
            except Exception as e:
                self.logger.error(f"Failed to send message: {e}")
                self._error_count += 1
                if self._error_handler:
                    self._error_handler(e)
                raise
    
    async def close(self) -> None:
        """Close WebSocket connection and cleanup resources."""
        if self._connection_state in (ConnectionState.CLOSING, ConnectionState.CLOSED):
            return
        
        self.logger.info("Closing WebSocket connection")
        await self._set_connection_state(ConnectionState.CLOSING)
        
        # Cancel background tasks
        await self._stop_background_tasks()
        
        # Close WebSocket
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")
        
        await self._set_connection_state(ConnectionState.CLOSED)
        self.logger.info("WebSocket connection closed")
    
    async def _stop_background_tasks(self) -> None:
        """Stop all background tasks."""
        tasks = [self._ping_task, self._listener_task, self._message_processor_task]
        
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        self._ping_task = None
        self._listener_task = None
        self._message_processor_task = None
        
        self.logger.debug("Background tasks stopped")
    
    async def _ping_loop(self) -> None:
        """Send periodic ping messages to maintain connection."""
        try:
            while self.is_connected:
                await asyncio.sleep(self._config.ping_interval)
                
                if not self.is_connected:
                    break
                
                try:
                    self._last_ping_time = time.time()
                    pong_waiter = await self._websocket.ping()
                    
                    # Wait for pong response
                    await asyncio.wait_for(pong_waiter, timeout=self._config.ping_timeout)
                    self._last_pong_time = time.time()
                    
                    self.logger.debug("Ping/pong successful")
                    
                except asyncio.TimeoutError:
                    self.logger.error("Ping timeout - connection may be dead")
                    await self._handle_connection_error(
                        Exception("Ping timeout")
                    )
                    break
                    
                except Exception as e:
                    self.logger.error(f"Ping failed: {e}")
                    await self._handle_connection_error(e)
                    break
                    
        except asyncio.CancelledError:
            self.logger.debug("Ping task cancelled")
        except Exception as e:
            self.logger.error(f"Ping loop error: {e}")
            await self._handle_connection_error(e)
    
    async def _message_listener(self) -> None:
        """Listen for incoming WebSocket messages."""
        try:
            while self.is_connected and self._websocket:
                try:
                    # Wait for incoming message
                    raw_message = await self._websocket.recv()
                    
                    # Create message object
                    if isinstance(raw_message, str):
                        message = WebSocketMessage(
                            message_type=MessageType.TEXT,
                            data=raw_message,
                            timestamp=time.time()
                        )
                    elif isinstance(raw_message, bytes):
                        message = WebSocketMessage(
                            message_type=MessageType.BINARY,
                            data=raw_message,
                            timestamp=time.time()
                        )
                    else:
                        self.logger.warning(f"Unknown message type: {type(raw_message)}")
                        continue
                    
                    # Queue message for processing
                    try:
                        self._message_queue.put_nowait(message)
                        self._message_count += 1
                    except asyncio.QueueFull:
                        self.logger.error("Message queue full, dropping message")
                    
                except ConnectionClosedError as e:
                    self.logger.info(f"Connection closed: {e}")
                    await self._set_connection_state(ConnectionState.CLOSED)
                    break
                    
                except Exception as e:
                    self.logger.error(f"Message listener error: {e}")
                    await self._handle_connection_error(e)
                    break
                    
        except asyncio.CancelledError:
            self.logger.debug("Message listener cancelled")
        except Exception as e:
            self.logger.error(f"Message listener fatal error: {e}")
            await self._handle_connection_error(e)
    
    async def _message_processor(self) -> None:
        """Process queued messages."""
        try:
            while True:
                try:
                    # Wait for message from queue
                    message = await self._message_queue.get()
                    
                    # Process message
                    if self._message_handler:
                        try:
                            self._message_handler(message)
                        except Exception as e:
                            self.logger.error(f"Message handler error: {e}")
                            if self._error_handler:
                                self._error_handler(e)
                    
                    # Mark task done
                    self._message_queue.task_done()
                    
                except asyncio.CancelledError:
                    # Drain remaining messages
                    while not self._message_queue.empty():
                        try:
                            self._message_queue.get_nowait()
                            self._message_queue.task_done()
                        except asyncio.QueueEmpty:
                            break
                    break
                    
        except Exception as e:
            self.logger.error(f"Message processor error: {e}")
            if self._error_handler:
                self._error_handler(e)
    
    async def _handle_connection_error(self, error: Exception) -> None:
        """Handle connection errors."""
        self.logger.error(f"Connection error: {error}")
        self._error_count += 1
        
        if self._error_handler:
            self._error_handler(error)
        
        await self._set_connection_state(ConnectionState.ERROR)
        
        # Attempt cleanup
        try:
            await self.close()
        except Exception as cleanup_error:
            self.logger.error(f"Cleanup error: {cleanup_error}")
    
    async def _set_connection_state(self, state: ConnectionState) -> None:
        """Update connection state and notify handler."""
        if self._connection_state != state:
            old_state = self._connection_state
            self._connection_state = state
            
            self.logger.debug(f"Connection state: {old_state.value} -> {state.value}")
            
            if self._connection_handler:
                try:
                    self._connection_handler(state)
                except Exception as e:
                    self.logger.error(f"Connection handler error: {e}")
    
    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information for monitoring."""
        return {
            "state": self._connection_state.value,
            "is_connected": self.is_connected,
            "url": self._config.url,
            "message_count": self._message_count,
            "error_count": self._error_count,
            "last_ping_time": self._last_ping_time,
            "last_pong_time": self._last_pong_time,
            "queue_size": self._message_queue.qsize(),
            "ping_interval": self._config.ping_interval,
        }


@asynccontextmanager
async def websocket_channel(config: WebSocketConfig):
    """Context manager for WebSocket channel lifecycle."""
    channel = WebSocketChannel(config)
    try:
        await channel.connect()
        yield channel
    finally:
        await channel.close()
```

### 3. Data Channel Implementation

#### src/session_manager_plugin/communicator/data_channel.py
```python
"""Data channel implementation for session data transfer."""

import asyncio
import logging
from typing import Optional, Callable, Any, Dict

from .websocket_channel import WebSocketChannel
from .types import WebSocketConfig, WebSocketMessage, MessageType, ConnectionState
from ..session.protocols import IDataChannel
from ..utils.logging import get_logger


class SessionDataChannel(IDataChannel):
    """Data channel implementation using WebSocket for session data transfer."""
    
    def __init__(self, config: WebSocketConfig):
        self.logger = get_logger(__name__)
        self._config = config
        self._channel: Optional[WebSocketChannel] = None
        self._input_handler: Optional[Callable[[bytes], None]] = None
        self._output_handler: Optional[Callable[[bytes], None]] = None
        
    async def open(self) -> bool:
        """Open the data channel connection."""
        try:
            self._channel = WebSocketChannel(self._config)
            
            # Set up message handling
            self._channel.set_message_handler(self._handle_message)
            self._channel.set_error_handler(self._handle_error)
            self._channel.set_connection_handler(self._handle_connection_change)
            
            # Connect
            success = await self._channel.connect()
            
            if success:
                self.logger.info("Data channel opened successfully")
            else:
                self.logger.error("Failed to open data channel")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error opening data channel: {e}")
            return False
    
    async def send_input_data(self, data: bytes) -> None:
        """Send input data through the channel."""
        if not self.is_open:
            raise RuntimeError("Data channel not open")
        
        try:
            await self._channel.send_message(data)
            self.logger.debug(f"Sent {len(data)} bytes of input data")
            
        except Exception as e:
            self.logger.error(f"Failed to send input data: {e}")
            raise
    
    async def close(self) -> None:
        """Close the data channel."""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self.logger.info("Data channel closed")
    
    @property
    def is_open(self) -> bool:
        """Check if channel is open."""
        return self._channel is not None and self._channel.is_connected
    
    def set_input_handler(self, handler: Callable[[bytes], None]) -> None:
        """Set handler for input data from remote."""
        self._input_handler = handler
    
    def set_output_handler(self, handler: Callable[[bytes], None]) -> None:
        """Set handler for output data to remote."""
        self._output_handler = handler
    
    def _handle_message(self, message: WebSocketMessage) -> None:
        """Handle incoming WebSocket message."""
        try:
            if message.message_type == MessageType.BINARY:
                if self._input_handler:
                    self._input_handler(message.data)
                else:
                    self.logger.debug(f"Received {len(message.data)} bytes (no handler)")
            
            elif message.message_type == MessageType.TEXT:
                # Convert text to bytes for consistent handling
                data = message.data.encode('utf-8')
                if self._input_handler:
                    self._input_handler(data)
                else:
                    self.logger.debug(f"Received text message (no handler): {message.data[:100]}...")
            
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
    
    def _handle_error(self, error: Exception) -> None:
        """Handle WebSocket errors."""
        self.logger.error(f"Data channel error: {error}")
    
    def _handle_connection_change(self, state: ConnectionState) -> None:
        """Handle connection state changes."""
        self.logger.info(f"Data channel connection state: {state.value}")
    
    def get_channel_info(self) -> Dict[str, Any]:
        """Get channel information."""
        if self._channel:
            return self._channel.get_connection_info()
        else:
            return {"state": "not_initialized", "is_open": False}
```

### 4. WebSocket Utilities and Helpers

#### src/session_manager_plugin/communicator/utils.py
```python
"""WebSocket communication utilities."""

import urllib.parse
from typing import Dict, Any, Optional

from .types import WebSocketConfig


def build_stream_url(base_url: str, session_id: str, token: str) -> str:
    """Build WebSocket stream URL with proper parameters."""
    if not base_url.startswith(('ws://', 'wss://')):
        # Convert HTTP(S) to WebSocket
        if base_url.startswith('https://'):
            base_url = base_url.replace('https://', 'wss://', 1)
        elif base_url.startswith('http://'):
            base_url = base_url.replace('http://', 'ws://', 1)
        else:
            # Assume HTTPS for security
            base_url = f"wss://{base_url}"
    
    # Parse URL and add parameters
    parsed = urllib.parse.urlparse(base_url)
    
    # Build query parameters
    params = {
        'sessionId': session_id,
        'token': token
    }
    
    query_string = urllib.parse.urlencode(params)
    
    # Reconstruct URL
    return urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        query_string,
        parsed.fragment
    ))


def create_websocket_config(
    stream_url: str,
    token: str,
    **kwargs: Any
) -> WebSocketConfig:
    """Create WebSocket configuration with sensible defaults."""
    return WebSocketConfig(
        url=stream_url,
        token=token,
        ping_interval=kwargs.get('ping_interval', 30.0),
        ping_timeout=kwargs.get('ping_timeout', 10.0),
        connect_timeout=kwargs.get('connect_timeout', 30.0),
        max_message_size=kwargs.get('max_message_size', 1024 * 1024),
        max_queue_size=kwargs.get('max_queue_size', 100),
        retry_attempts=kwargs.get('retry_attempts', 3),
        retry_delay=kwargs.get('retry_delay', 1.0)
    )


def validate_websocket_url(url: str) -> bool:
    """Validate WebSocket URL format."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ('ws', 'wss') and parsed.netloc
    except Exception:
        return False
```

## Testing Requirements

### Unit Tests Structure
```
tests/test_communicator/
├── test_websocket_channel.py
├── test_data_channel.py
├── test_types.py
└── test_utils.py
```

### Integration Tests
- WebSocket connection establishment
- Message sending and receiving
- Ping/pong mechanism
- Error handling and reconnection
- Connection lifecycle management

## Validation Steps

1. **Test WebSocket connection:**
   ```python
   config = WebSocketConfig(
       url="wss://example.com/stream",
       token="test-token"
   )
   channel = WebSocketChannel(config)
   ```

2. **Test message handling and ping mechanism**
3. **Verify error handling and reconnection logic**
4. **Test data channel integration**

## Success Criteria

- [x] WebSocket connection establishment and lifecycle management
- [x] Robust message handling with proper queuing
- [x] Ping/pong health check mechanism
- [x] Thread-safe operations using asyncio
- [x] Comprehensive error handling and retry logic
- [x] Integration with session data channel interface
- [x] Unit and integration tests passing

## Next Phase

Proceed to [Phase 4: Integration & CLI Interface](03_integration_cli.md) once WebSocket communication is fully implemented and tested.