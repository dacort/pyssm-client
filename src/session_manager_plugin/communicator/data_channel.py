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
        self._closed_handler: Optional[Callable[[], None]] = None
        
        # AWS SSM protocol state tracking
        self._expected_sequence_number = 0
        self._initial_output_received = False
        # Outbound input sequence number (SSM expects monotonically increasing values starting at 0)
        self._out_seq = 0
        # Client metadata for handshake
        self._client_id: Optional[str] = None
        self._client_version: str = "python-session-manager-plugin/0.1.0"
        # Flow control: input gating
        self._input_allowed: bool = True
        # Handshake session metadata
        self._session_type: Optional[str] = None
        self._session_properties: Optional[dict] = None
        # Input coalescing (disabled by default; better for TTY interactivity)
        self._coalesce_enabled: bool = False
        self._input_buffer = bytearray()
        self._flush_task: Optional[asyncio.Task] = None  # type: ignore[name-defined]
        self._flush_delay_sec: float = 0.01
        # Out-of-order output buffering
        self._incoming_buffer: Dict[int, bytes] = {}

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

    def set_client_info(self, client_id: Optional[str], client_version: Optional[str] = None) -> None:
        """Set client metadata used during handshake."""
        if client_id:
            self._client_id = client_id
        if client_version:
            self._client_version = client_version

    async def send_input_data(self, data: bytes) -> None:
        """Send input data through the channel."""
        if not self.is_open or self._channel is None:
            raise RuntimeError("Data channel not open")

        try:
            if not self._input_allowed:
                self.logger.debug("Input paused by remote (pause_publication); dropping input")
                return
            # Normalize line endings to match SSM expectations
            normalized = self._normalize_input(data)

            # Control bytes should be flushed immediately
            control_bytes = {b"\x03", b"\x1a", b"\x1c"}
            if self._coalesce_enabled:
                # If control byte, flush buffer then send immediately
                if len(normalized) == 1 and normalized in control_bytes:
                    await self._flush_input_buffer()
                    await self._send_input_now(normalized)
                    return

                # Append to buffer
                self._input_buffer.extend(normalized)

                # If newline (CR) present or buffer large, flush immediately
                if b"\r" in normalized or len(self._input_buffer) >= 512:
                    await self._flush_input_buffer()
                    return

                # Otherwise, debounce flush
                self._schedule_flush()
                return

            # No coalescing: send directly
            await self._send_input_now(normalized)

        except Exception as e:
            self.logger.error(f"Failed to send input data: {e}")
            raise

    def _schedule_flush(self) -> None:
        try:
            import asyncio as _asyncio
            if self._flush_task and not self._flush_task.done():
                self._flush_task.cancel()
            async def _wait_and_flush():
                try:
                    await _asyncio.sleep(self._flush_delay_sec)
                    await self._flush_input_buffer()
                except _asyncio.CancelledError:
                    pass
            self._flush_task = _asyncio.create_task(_wait_and_flush())
        except Exception as e:
            self.logger.debug(f"Failed to schedule flush: {e}")

    async def _flush_input_buffer(self) -> None:
        if not self._input_buffer:
            return
        buf = bytes(self._input_buffer)
        self._input_buffer.clear()
        await self._send_input_now(buf)

    async def _send_input_now(self, payload: bytes) -> None:
        # For debugging: log what we're sending
        self.logger.debug(f"Sending {len(payload)} bytes: {payload[:50]}")
        # Format as AWS SSM input stream message with correct payload type and sequence
        if not self.is_open or self._channel is None:
            return
        input_message = self._create_input_stream_message(payload)
        await self._channel.send_message(input_message)
        self.logger.debug(f"Sent {len(payload)} bytes of input data as AWS SSM input stream message")

    async def close(self) -> None:
        """Close the data channel."""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self.logger.info("Data channel closed")
        # Cancel pending flush
        try:
            if self._flush_task and not self._flush_task.done():
                self._flush_task.cancel()
        except Exception:
            pass

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

    def set_closed_handler(self, handler: Callable[[], None]) -> None:
        """Set handler called when the channel closes or errors."""
        self._closed_handler = handler

    def set_coalescing(self, enabled: bool, delay_sec: Optional[float] = None) -> None:
        """Configure input coalescing behavior."""
        self._coalesce_enabled = enabled
        if delay_sec is not None:
            self._flush_delay_sec = max(0.0, float(delay_sec))

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
                            # Log session type if known
                            if self._session_type:
                                self.logger.info(f"Handshake: session_type={self._session_type}")
                            message_processed = True
                        elif client_message.payload_type == PayloadType.ENC_CHALLENGE_REQUEST:
                            # Not supported; log only
                            self.logger.info("Encryption challenge not supported; ignoring.")
                            message_processed = True
                        # Shell and stderr output
                        elif client_message.message_type.strip() == "channel_closed":
                            # Friendly notice then close
                            try:
                                import json as _json
                                payload = _json.loads(client_message.payload.decode('utf-8', errors='ignore'))
                                output = payload.get('Output') or payload.get('output') or "Session closed."
                                sess_id = payload.get('SessionId') or payload.get('sessionId')
                            except Exception:
                                output = "Session closed."
                                sess_id = None
                            if not getattr(self, "_closed_message_printed", False) and self._input_handler:
                                if sess_id:
                                    msg = f"\n\nSessionId: {sess_id} : {output}\n\n"
                                else:
                                    msg = f"\n\n{output}\n\n"
                                self._input_handler(msg.encode('utf-8'))
                                self._closed_message_printed = True
                            # Trigger close once
                            self._trigger_closed()
                            message_processed = True
                        elif client_message.message_type.strip() == "start_publication":
                            self._input_allowed = True
                            self.logger.debug("Received start_publication; input allowed")
                            message_processed = True
                        elif client_message.message_type.strip() == "pause_publication":
                            self._input_allowed = False
                            self.logger.debug("Received pause_publication; input paused")
                            message_processed = True
                        elif client_message.is_shell_output():
                            seq = client_message.sequence_number
                            if seq == self._expected_sequence_number:
                                # Display now
                                shell_data = client_message.get_shell_data()
                                if shell_data and self._input_handler:
                                    self._input_handler(shell_data.encode('utf-8'))
                                message_processed = True
                            elif seq > self._expected_sequence_number:
                                # Buffer for later; still ack
                                self._incoming_buffer[seq] = message.data  # type: ignore[arg-type]
                                message_processed = True
                            else:
                                # Old message; ignore
                                message_processed = False
                        
                        # Handle AWS SSM sequence tracking properly
                        if message_processed:
                            # Only acknowledge applicable messages (not ack or channel_closed or start/pause publication)
                            if client_message.message_type not in ("acknowledge", "channel_closed", "start_publication", "pause_publication"):
                                self._schedule_acknowledgment(client_message)

                            # Update expected sequence only for output stream messages
                            if client_message.message_type == "output_stream_data":
                                if client_message.sequence_number == self._expected_sequence_number:
                                    self._expected_sequence_number += 1
                                    self.logger.debug(
                                        f"Updated expected sequence to {self._expected_sequence_number}"
                                    )
                                    # Process any buffered messages now in order
                                    self._drain_buffered_output()
                                elif client_message.sequence_number > self._expected_sequence_number:
                                    # Out-of-order; keep expected and rely on buffered processing
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

    def _drain_buffered_output(self) -> None:
        """Print buffered output messages in order starting from expected sequence."""
        try:
            while self._expected_sequence_number in self._incoming_buffer:
                raw = self._incoming_buffer.pop(self._expected_sequence_number)
                cm = parse_client_message(raw)
                if cm and cm.is_shell_output():
                    shell_data = cm.get_shell_data()
                    if shell_data and self._input_handler:
                        self._input_handler(shell_data.encode('utf-8'))
                self._expected_sequence_number += 1
                self.logger.debug(f"Drained buffered seq; expected now {self._expected_sequence_number}")
        except Exception as e:
            self.logger.debug(f"Drain buffer failed: {e}")

    def _handle_error(self, error: Exception) -> None:
        """Handle WebSocket errors."""
        self.logger.error(f"Data channel error: {error}")

    def _handle_connection_change(self, state: ConnectionState) -> None:
        """Handle connection state changes."""
        self.logger.info(f"Data channel connection state: {state.value}")
        if state in (ConnectionState.CLOSED, ConnectionState.ERROR):
            self._trigger_closed()

    def _trigger_closed(self) -> None:
        """Invoke closed handler exactly once."""
        if getattr(self, "_closed_invoked", False):
            return
        # Initialize guard attributes if missing (for forward compatibility)
        self._closed_invoked = True
        try:
            if self._closed_handler:
                self._closed_handler()
        except Exception as e:
            self.logger.debug(f"Closed handler error: {e}")

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
                # Include optional fields when available (matches Go behavior)
                "ClientId": self._client_id or "",
                "ClientVersion": self._client_version,
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
            if not self.is_open or self._channel is None:
                return
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
                        # Capture session type + properties
                        ap = action.get("ActionParameters") or {}
                        if isinstance(ap, str):
                            try:
                                import json as _json2
                                ap = _json2.loads(ap)
                            except Exception:
                                ap = {}
                        self._session_type = ap.get("SessionType") or self._session_type or ""
                        self._session_properties = ap.get("Properties") or {}
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
