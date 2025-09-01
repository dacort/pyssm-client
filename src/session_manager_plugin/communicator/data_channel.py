"""Data channel implementation for session data transfer."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Optional

from ..session.protocols import IDataChannel
from ..utils.logging import get_logger
from .protocol import parse_client_message, create_acknowledge_message, PayloadType, serialize_client_message_with_payload_type
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
        
        # AWS SSM protocol state tracking
        self._expected_sequence_number = 0
        self._initial_output_received = False
        # Outbound input sequence number (SSM expects monotonically increasing values starting at 0)
        self._out_seq = 0

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
            # Normalize line endings to match SSM expectations
            normalized = self._normalize_input(data)

            # For debugging: log what we're sending
            self.logger.debug(f"Sending {len(normalized)} bytes: {normalized[:50]}")

            # Format as AWS SSM input stream message with correct payload type and sequence
            input_message = self._create_input_stream_message(normalized)
            await self._channel.send_message(input_message)
            self.logger.debug(f"Sent {len(data)} bytes of input data as AWS SSM input stream message")

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
                            f"payload_length={client_message.payload_length}, "
                            f"sequence={client_message.sequence_number} (expected={self._expected_sequence_number})"
                        )
                        
                        message_processed = False

                        # Handshake and control payloads
                        if client_message.payload_type == PayloadType.HANDSHAKE_REQUEST:
                            self._schedule_handshake_response(client_message)
                            message_processed = True
                        elif client_message.payload_type == PayloadType.HANDSHAKE_COMPLETE:
                            # Optionally display customer message
                            try:
                                import json as _json
                                payload = _json.loads(client_message.payload.decode('utf-8', errors='ignore'))
                                cust_msg = payload.get('CustomerMessage') or payload.get('customerMessage')
                                if cust_msg and self._input_handler:
                                    self._input_handler((cust_msg + "\n").encode('utf-8'))
                            except Exception as e:
                                self.logger.debug(f"Failed to parse HandshakeComplete payload: {e}")
                            message_processed = True
                        elif client_message.payload_type == PayloadType.ENC_CHALLENGE_REQUEST:
                            # Not supported; log only
                            self.logger.info("Encryption challenge not supported; ignoring.")
                            message_processed = True
                        # Shell and stderr output
                        elif client_message.is_shell_output():
                            shell_data = client_message.get_shell_data()
                            if shell_data and self._input_handler:
                                # Send only the shell content as bytes
                                self._input_handler(shell_data.encode('utf-8'))
                            message_processed = True
                        else:
                            # Other message types: acknowledge but do not display
                            message_processed = True
                        
                        # Handle AWS SSM sequence tracking properly
                        if message_processed:
                            # Only acknowledge non-ack messages
                            if client_message.message_type != "acknowledge":
                                self._schedule_acknowledgment(client_message)

                            # Update expected sequence only for output stream messages
                            if client_message.message_type == "output_stream_data":
                                if client_message.sequence_number == self._expected_sequence_number:
                                    self._expected_sequence_number += 1
                                    self.logger.debug(
                                        f"Updated expected sequence to {self._expected_sequence_number}"
                                    )
                                elif client_message.sequence_number > self._expected_sequence_number:
                                    # Out-of-order; keep expected and rely on agent resends
                                    self.logger.debug(
                                        f"Received future sequence {client_message.sequence_number}, expected {self._expected_sequence_number}"
                                    )
                                else:
                                    self.logger.debug(
                                        f"Received older sequence {client_message.sequence_number}, keeping expected at {self._expected_sequence_number}"
                                    )
                    else:
                        # Fallback for unparseable messages
                        self.logger.debug(f"Failed to parse binary message: {len(message.data)} bytes")
                        if self._input_handler:
                            self._input_handler(message.data)

            elif message.message_type == MessageType.TEXT:
                # Text frames are control/handshake; do not print to user
                if isinstance(message.data, str):
                    self.logger.debug(f"Text message: {message.data[:200]}...")

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
            info = self._channel.get_connection_info()
            info["expected_sequence_number"] = self._expected_sequence_number
            return info
        else:
            return {
                "state": "not_initialized", 
                "is_open": False,
                "expected_sequence_number": self._expected_sequence_number
            }

    async def _send_handshake_initialization(self) -> None:
        """Send AWS SSM protocol handshake initialization message."""
        try:
            # Create handshake message as per AWS SSM protocol
            handshake_message = {
                "MessageSchemaVersion": 1,
                "RequestId": str(uuid.uuid4()),
                "TokenValue": self._config.token,
            }
            
            # Send as JSON text message
            message_json = json.dumps(handshake_message)
            await self._channel.send_message(message_json)
            
            self.logger.debug(f"Sent handshake initialization: RequestId={handshake_message['RequestId']}")
            
        except Exception as e:
            self.logger.error(f"Failed to send handshake initialization: {e}")
            raise
    
    def _schedule_acknowledgment(self, original_message) -> None:
        """Schedule acknowledgment message to be sent asynchronously."""
        import asyncio
        try:
            # Schedule the acknowledgment to be sent in the next event loop iteration
            asyncio.create_task(self._send_acknowledgment(original_message))
        except Exception as e:
            self.logger.error(f"Failed to schedule acknowledgment: {e}")

    def _schedule_handshake_response(self, original_message) -> None:
        import asyncio
        try:
            asyncio.create_task(self._send_handshake_response(original_message))
        except Exception as e:
            self.logger.error(f"Failed to schedule handshake response: {e}")
    
    async def _send_acknowledgment(self, original_message) -> None:
        """Send acknowledgment message for received message."""
        try:
            # Create acknowledgment message
            ack_message = create_acknowledge_message(original_message)
            
            # Send acknowledgment through WebSocket
            if self._channel:
                await self._channel.send_message(ack_message)
                self.logger.debug(
                    f"Sent acknowledgment for message: type={original_message.message_type}, "
                    f"id={original_message.get_message_id_string()[:8]}, "
                    f"seq={original_message.sequence_number}"
                )
            else:
                self.logger.error("Cannot send acknowledgment: channel not available")
                
        except Exception as e:
            self.logger.error(f"Failed to send acknowledgment: {e}")
            # Don't raise - acknowledgment failure shouldn't stop message processing

    async def _send_handshake_response(self, original_message) -> None:
        """Send HandshakeResponse payload for a HandshakeRequest."""
        try:
            import json as _json

            # Default response with no processed actions
            response = {
                "ClientVersion": "python-session-manager-plugin/0.1.0",
                "ProcessedClientActions": [],
                "Errors": [],
            }

            # Attempt to parse request and respond per action
            try:
                request = _json.loads(original_message.payload.decode('utf-8', errors='ignore'))
                actions = request.get("RequestedClientActions", [])
                for action in actions:
                    atype = action.get("ActionType")
                    processed = {"ActionType": atype, "ActionStatus": 1}  # Success
                    if atype == "SessionType":
                        # Echo back minimal result
                        processed["ActionResult"] = action.get("ActionParameters")
                    elif atype == "KMSEncryption":
                        processed["ActionStatus"] = 3  # Unsupported
                        processed["Error"] = "KMSEncryption not supported in Python client"
                    else:
                        processed["ActionStatus"] = 3
                        processed["Error"] = f"Unsupported action {atype}"
                    response["ProcessedClientActions"].append(processed)
            except Exception as e:
                self.logger.debug(f"Failed to parse HandshakeRequest payload: {e}")

            payload = _json.dumps(response).encode('utf-8')
            msg = self._serialize_input_message_with_payload_type(payload, PayloadType.HANDSHAKE_RESPONSE)
            if self._channel:
                await self._channel.send_message(msg)
                self.logger.debug("Sent HandshakeResponse")
        except Exception as e:
            self.logger.error(f"Failed to send HandshakeResponse: {e}")
    
    def _serialize_input_message_with_payload_type(self, input_data: bytes, payload_type: int) -> bytes:
        """Serialize input message with specific payload type."""
        import time
        import uuid
        
        # Note: line ending normalization handled earlier
        message_uuid = uuid.uuid4()
        created_date = int(time.time() * 1000)

        # Capture current outbound sequence and then advance
        seq = self._out_seq
        self._out_seq += 1

        return serialize_client_message_with_payload_type(
            message_type="input_stream_data", 
            schema_version=1,
            created_date=created_date,
            sequence_number=seq,
            flags=0,
            message_id=message_uuid.bytes,
            payload_type=payload_type,
            payload=input_data
        )
    
    def _schedule_shell_input(self, data: bytes) -> None:
        """Deprecated: previously used for experimental auto-input; now a no-op."""
        self.logger.debug("_schedule_shell_input is deprecated and will be ignored.")
    
    def _create_input_stream_message(self, input_data: bytes) -> bytes:
        """Create AWS SSM input stream message for keyboard input."""
        # Use OUTPUT payload type for normal keyboard input (matches Go plugin)
        return self._serialize_input_message_with_payload_type(input_data, PayloadType.OUTPUT)

    def _normalize_input(self, data: bytes) -> bytes:
        """Normalize line endings for SSM: map LF and CRLF to CR."""
        # Replace CRLF with CR
        data = data.replace(b"\r\n", b"\r")
        # Replace lone LF with CR
        data = data.replace(b"\n", b"\r")
        return data

    async def send_terminal_size(self, cols: int, rows: int) -> None:
        """Send terminal size update using SIZE payload type."""
        if not self.is_open or self._channel is None:
            return
        try:
            payload = json.dumps({"cols": int(cols), "rows": int(rows)}).encode("utf-8")
            msg = self._serialize_input_message_with_payload_type(payload, PayloadType.SIZE)
            await self._channel.send_message(msg)
            self.logger.debug(f"Sent terminal size: cols={cols}, rows={rows}")
        except Exception as e:
            self.logger.error(f"Failed to send terminal size: {e}")
