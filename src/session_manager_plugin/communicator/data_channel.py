"""Data channel implementation for session data transfer."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Optional

from ..session.protocols import IDataChannel
from ..utils.logging import get_logger
from .protocol import parse_client_message
from .types import ConnectionState, MessageType, WebSocketConfig, WebSocketMessage
from .websocket_channel import WebSocketChannel


class SessionDataChannel(IDataChannel):
    """Data channel implementation using WebSocket for session data transfer."""

    def __init__(self, config: WebSocketConfig) -> None:
        """Initialize data channel with WebSocket configuration."""
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
                # Send AWS SSM protocol handshake initialization
                await self._send_handshake_initialization()
                self.logger.info("Data channel opened successfully")
            else:
                self.logger.error("Failed to open data channel")

            return success

        except Exception as e:
            self.logger.error(f"Error opening data channel: {e}")
            return False

    async def send_input_data(self, data: bytes) -> None:
        """Send input data through the channel."""
        if not self.is_open or self._channel is None:
            raise RuntimeError("Data channel not open")

        try:
            # For debugging: log what we're sending
            self.logger.debug(f"Sending {len(data)} bytes: {data[:50]}")
            
            # Send raw bytes directly (AWS SSM protocol handles binary data)
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
                # Parse AWS SSM binary protocol
                if isinstance(message.data, bytes):
                    client_message = parse_client_message(message.data)
                    
                    if client_message:
                        self.logger.debug(
                            f"Parsed AWS SSM message: type={client_message.message_type}, "
                            f"payload_type={client_message.payload_type}, "
                            f"payload_length={client_message.payload_length}"
                        )
                        
                        # Handle shell output data
                        if client_message.is_shell_output():
                            shell_data = client_message.get_shell_data()
                            if shell_data and self._input_handler:
                                # Send only the shell content as bytes
                                self._input_handler(shell_data.encode('utf-8'))
                            elif shell_data:
                                self.logger.debug(f"Shell output (no handler): {shell_data[:100]}")
                        else:
                            # Handle other message types (handshakes, etc.)
                            self.logger.debug(
                                f"Non-shell message: type={client_message.message_type}, "
                                f"payload_type={client_message.payload_type}"
                            )
                    else:
                        # Fallback for unparseable messages
                        self.logger.debug(f"Failed to parse binary message: {len(message.data)} bytes")
                        if self._input_handler:
                            self._input_handler(message.data)

            elif message.message_type == MessageType.TEXT:
                # Handle text messages (handshake responses, etc.)
                if isinstance(message.data, str):
                    self.logger.debug(f"Text message: {message.data[:200]}...")
                    
                    # Convert text to bytes for consistent handling
                    data = message.data.encode("utf-8")
                    if self._input_handler:
                        self._input_handler(data)

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

    async def _send_handshake_initialization(self) -> None:
        """Send AWS SSM protocol handshake initialization message."""
        try:
            # Create handshake message as per AWS SSM protocol
            handshake_message = {
                "MessageSchemaVersion": "1.0",
                "RequestId": str(uuid.uuid4()),
                "TokenValue": self._config.token
            }
            
            # Send as JSON text message
            message_json = json.dumps(handshake_message)
            await self._channel.send_message(message_json)
            
            self.logger.debug(f"Sent handshake initialization: RequestId={handshake_message['RequestId']}")
            
        except Exception as e:
            self.logger.error(f"Failed to send handshake initialization: {e}")
            raise
