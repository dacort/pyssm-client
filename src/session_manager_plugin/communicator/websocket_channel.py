"""WebSocket channel implementation for AWS Session Manager communication."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import websockets
from websockets import ClientConnection
from websockets.exceptions import ConnectionClosed, WebSocketException

from ..utils.logging import get_logger
from .types import (
    ConnectionHandler,
    ConnectionState,
    ErrorHandler,
    MessageHandler,
    MessageType,
    WebSocketConfig,
    WebSocketMessage,
)


class WebSocketChannel:
    """WebSocket channel for AWS Session Manager communication."""

    def __init__(self, config: WebSocketConfig) -> None:
        """Initialize WebSocket channel with configuration."""
        self._logger = get_logger(__name__)
        self._config = config
        self._websocket: Optional[ClientConnection] = None
        self._connection_state = ConnectionState.DISCONNECTED

        # Event handlers
        self._message_handler: Optional[MessageHandler] = None
        self._error_handler: Optional[ErrorHandler] = None
        self._connection_handler: Optional[ConnectionHandler] = None

        # Background tasks
        self._ping_task: Optional[asyncio.Task] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue[WebSocketMessage] = asyncio.Queue(
            maxsize=config.max_queue_size
        )

        # Connection coordination
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()

        self._logger.debug(f"WebSocket channel initialized for {config.url}")

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return (
            self._connection_state == ConnectionState.CONNECTED
            and self._websocket is not None
            and self._websocket.state.name == 'OPEN'
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
                self._logger.debug("Already connected")
                return True

            for attempt in range(self._config.retry_attempts):
                try:
                    await self._attempt_connection()
                    return True
                except Exception as e:
                    self._logger.warning(
                        f"Connection attempt {attempt + 1} failed: {e}"
                    )
                    if attempt < self._config.retry_attempts - 1:
                        await asyncio.sleep(self._config.retry_delay * (attempt + 1))

            self._logger.error("All connection attempts failed")
            await self._set_connection_state(ConnectionState.ERROR)
            return False

    async def _attempt_connection(self) -> None:
        """Attempt a single WebSocket connection."""
        self._logger.info(f"Connecting to {self._config.url}")
        await self._set_connection_state(ConnectionState.CONNECTING)

        # Prepare connection headers
        headers = {
            "Authorization": f"Bearer {self._config.token}",
            "User-Agent": "python-session-manager-plugin/0.1.0",
        }

        try:
            self._websocket = await asyncio.wait_for(
                websockets.connect(
                    self._config.url,
                    extra_headers=headers,
                    max_size=self._config.max_message_size,
                    ping_interval=None,  # We handle pings manually
                    ping_timeout=None,
                ),
                timeout=self._config.connect_timeout,
            )

            await self._set_connection_state(ConnectionState.CONNECTED)
            self._logger.info("WebSocket connection established")

            # Start background tasks
            await self._start_background_tasks()

        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Connection timeout after {self._config.connect_timeout}s"
            )
        except Exception as e:
            raise RuntimeError(f"Connection failed: {e}") from e

    async def _start_background_tasks(self) -> None:
        """Start background tasks for ping and message handling."""
        self._ping_task = asyncio.create_task(self._ping_loop())
        self._listener_task = asyncio.create_task(self._message_listener())
        self._logger.debug("Background tasks started")

    async def send_message(self, data: str | bytes | dict) -> None:
        """Send message through WebSocket."""
        if not self.is_connected or self._websocket is None:
            raise RuntimeError("WebSocket not connected")

        async with self._send_lock:
            try:
                if isinstance(data, dict):
                    message_data = json.dumps(data)
                    await self._websocket.send(message_data)
                elif isinstance(data, (str, bytes)):
                    await self._websocket.send(data)
                else:
                    raise ValueError(f"Unsupported message type: {type(data)}")

                self._logger.debug(f"Sent message: {len(str(data))} chars/bytes")

            except ConnectionClosed as e:
                self._logger.error(f"Connection closed while sending: {e}")
                await self._set_connection_state(ConnectionState.CLOSED)
                raise
            except Exception as e:
                self._logger.error(f"Failed to send message: {e}")
                if self._error_handler:
                    self._error_handler(e)
                raise

    async def close(self) -> None:
        """Close WebSocket connection and cleanup resources."""
        if self._connection_state in (ConnectionState.CLOSING, ConnectionState.CLOSED):
            return

        self._logger.info("Closing WebSocket connection")
        await self._set_connection_state(ConnectionState.CLOSING)

        # Cancel background tasks
        await self._stop_background_tasks()

        # Close WebSocket
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                self._logger.error(f"Error closing WebSocket: {e}")

        await self._set_connection_state(ConnectionState.CLOSED)
        self._logger.info("WebSocket connection closed")

    async def _stop_background_tasks(self) -> None:
        """Stop all background tasks."""
        tasks = [self._ping_task, self._listener_task]

        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._ping_task = None
        self._listener_task = None
        self._logger.debug("Background tasks stopped")

    async def _ping_loop(self) -> None:
        """Send periodic ping messages to maintain connection."""
        try:
            while self.is_connected:
                await asyncio.sleep(self._config.ping_interval)

                if not self.is_connected:
                    break

                try:
                    if self._websocket is None:
                        break
                    pong_waiter = await self._websocket.ping()
                    await asyncio.wait_for(
                        pong_waiter, timeout=self._config.ping_timeout
                    )
                    self._logger.debug("Ping/pong successful")

                except asyncio.TimeoutError:
                    self._logger.error("Ping timeout - connection may be dead")
                    await self._handle_connection_error(Exception("Ping timeout"))
                    break
                except Exception as e:
                    self._logger.error(f"Ping failed: {e}")
                    await self._handle_connection_error(e)
                    break

        except asyncio.CancelledError:
            self._logger.debug("Ping task cancelled")

    async def _message_listener(self) -> None:
        """Listen for incoming WebSocket messages."""
        try:
            while self.is_connected and self._websocket:
                try:
                    raw_message = await self._websocket.recv()

                    # Create message object
                    if isinstance(raw_message, str):
                        message = WebSocketMessage(
                            message_type=MessageType.TEXT, data=raw_message
                        )
                    elif isinstance(raw_message, bytes):
                        message = WebSocketMessage(
                            message_type=MessageType.BINARY, data=raw_message
                        )
                    else:
                        self._logger.warning(
                            f"Unknown message type: {type(raw_message)}"
                        )
                        continue

                    # Handle message
                    if self._message_handler:
                        try:
                            self._message_handler(message)
                        except Exception as e:
                            self._logger.error(f"Message handler error: {e}")
                            if self._error_handler:
                                self._error_handler(e)

                except ConnectionClosed:
                    self._logger.info("WebSocket connection closed by remote")
                    await self._set_connection_state(ConnectionState.CLOSED)
                    break
                except Exception as e:
                    self._logger.error(f"Message listener error: {e}")
                    await self._handle_connection_error(e)
                    break

        except asyncio.CancelledError:
            self._logger.debug("Message listener cancelled")

    async def _handle_connection_error(self, error: Exception) -> None:
        """Handle connection errors."""
        self._logger.error(f"Connection error: {error}")

        if self._error_handler:
            self._error_handler(error)

        await self._set_connection_state(ConnectionState.ERROR)

        # Cleanup
        try:
            await self.close()
        except Exception as cleanup_error:
            self._logger.error(f"Cleanup error: {cleanup_error}")

    async def _set_connection_state(self, state: ConnectionState) -> None:
        """Update connection state and notify handler."""
        if self._connection_state != state:
            old_state = self._connection_state
            self._connection_state = state

            self._logger.debug(f"Connection state: {old_state.value} -> {state.value}")

            if self._connection_handler:
                try:
                    self._connection_handler(state)
                except Exception as e:
                    self._logger.error(f"Connection handler error: {e}")

    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information for monitoring."""
        return {
            "state": self._connection_state.value,
            "is_connected": self.is_connected,
            "url": self._config.url,
            "queue_size": self._message_queue.qsize(),
            "ping_interval": self._config.ping_interval,
        }
