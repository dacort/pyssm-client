"""Data channel implementation for session data transfer."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict

from ..session.protocols import IDataChannel
from ..utils.logging import get_logger
from .protocol import (
    create_acknowledge_message,
    serialize_client_message,
)
from .message_parser import MessageParser, ParsedMessageType
from .message_handlers import MessageRouter, MessageHandlerContext
from ..constants import (
    PayloadType,
    CLIENT_VERSION,
    MESSAGE_INPUT_STREAM,
    MESSAGE_OUTPUT_STREAM,
    MESSAGE_ACKNOWLEDGE,
    MESSAGE_CHANNEL_CLOSED,
    MESSAGE_START_PUBLICATION,
    MESSAGE_PAUSE_PUBLICATION,
)
from .types import ConnectionState, WebSocketConfig, WebSocketMessage
from .websocket_channel import WebSocketChannel


class SessionDataChannel(IDataChannel):
    """Data channel implementation using WebSocket for session data transfer."""

    def __init__(self, config: WebSocketConfig) -> None:
        """Initialize data channel with WebSocket configuration."""
        self.logger = get_logger(__name__)
        self._config = config
        self._channel: WebSocketChannel | None = None
        self._input_handler: Callable[[bytes], None] | None = None
        self._output_handler: Callable[[bytes], None] | None = None
        self._closed_handler: Callable[[], None] | None = None
        # NEW: separate stream handlers (optional)
        self._stdout_handler: Callable[[bytes], None] | None = None
        self._stderr_handler: Callable[[bytes], None] | None = None

        # AWS SSM protocol state tracking
        self._expected_sequence_number = 0
        self._initial_output_received = False
        # Outbound input sequence number (SSM expects monotonically increasing values starting at 0)
        self._out_seq = 0
        # Client metadata for handshake
        self._client_id: str | None = None
        self._client_version: str = CLIENT_VERSION
        # Flow control: input gating
        self._input_allowed: bool = True
        # Handshake session metadata
        self._session_type: str | None = None
        self._session_properties: dict[str, Any] | None = None
        self._agent_version: str | None = None
        # Input coalescing (disabled by default; better for TTY interactivity)
        self._coalesce_enabled: bool = False
        self._input_buffer = bytearray()
        self._flush_task: asyncio.Task | None = None  # type: ignore[name-defined]
        self._flush_delay_sec: float = 0.01
        # Out-of-order output buffering
        self._incoming_buffer: Dict[int, bytes] = {}

        # Message processing components
        self._message_parser = MessageParser()
        self._message_router = MessageRouter()

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
                self.logger.debug("Data channel opened successfully")
            else:
                self.logger.error("Failed to open data channel")

            return success

        except Exception as e:
            self.logger.error(f"Error opening data channel: {e}")
            return False

    def set_client_info(
        self, client_id: str | None, client_version: str | None = None
    ) -> None:
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
                self.logger.debug(
                    "Input paused by remote (pause_publication); dropping input"
                )
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
            if self._flush_task and not self._flush_task.done():
                self._flush_task.cancel()

            async def _wait_and_flush() -> None:
                try:
                    await asyncio.sleep(self._flush_delay_sec)
                    await self._flush_input_buffer()
                except asyncio.CancelledError:
                    pass

            self._flush_task = asyncio.create_task(_wait_and_flush())
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
        self.logger.debug(f"Sending {len(payload)} bytes: {payload[:50]!r}")
        # Format as AWS SSM input stream message with correct payload type and sequence
        if not self.is_open or self._channel is None:
            return
        input_message = self._create_input_stream_message(payload)
        await self._channel.send_message(input_message)
        self.logger.debug(
            f"Sent {len(payload)} bytes of input data as AWS SSM input stream message"
        )

    async def close(self) -> None:
        """Close the data channel."""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self.logger.debug("Data channel closed")
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

    def set_stdout_handler(self, handler: Callable[[bytes], None]) -> None:
        """Set handler for stdout data from remote."""
        self._stdout_handler = handler

    def set_stderr_handler(self, handler: Callable[[bytes], None]) -> None:
        """Set handler for stderr data from remote."""
        self._stderr_handler = handler

    def set_coalescing(self, enabled: bool, delay_sec: float | None = None) -> None:
        """Configure input coalescing behavior."""
        self._coalesce_enabled = enabled
        if delay_sec is not None:
            self._flush_delay_sec = max(0.0, float(delay_sec))

    def _handle_message(self, message: WebSocketMessage) -> None:
        """Handle incoming WebSocket message using new parser/handler architecture."""
        try:
            # Parse the message
            parsed_message = self._message_parser.parse_websocket_message(message)
            if not parsed_message:
                return

            # Log parsed message details
            if parsed_message.client_message:
                client_message = parsed_message.client_message
                self.logger.debug(
                    f"Parsed AWS SSM message: type={client_message.message_type}, "
                    f"payload_type={client_message.payload_type}, "
                    f"payload_length={client_message.payload_length}, "
                    f"sequence={client_message.sequence_number} (expected={self._expected_sequence_number})"
                )

            # Create handler context
            context = MessageHandlerContext(
                send_message=self._async_send_message,
                trigger_closed=self._trigger_closed,
                input_handler=self._input_handler,
                stdout_handler=self._stdout_handler,
                stderr_handler=self._stderr_handler,
            )

            # Route to appropriate handler
            asyncio.create_task(self._process_parsed_message(parsed_message, context))

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    async def _process_parsed_message(
        self, parsed_message: Any, context: MessageHandlerContext
    ) -> None:
        """Process a parsed message asynchronously."""
        try:
            processed, new_seq, input_change = await self._message_router.route_message(
                parsed_message,
                context,
                self._expected_sequence_number,
                self._serialize_input_message_with_payload_type,
            )

            if not processed:
                return

            # Handle input permission changes
            if input_change is not None:
                if parsed_message.message_type == ParsedMessageType.START_PUBLICATION:
                    self._input_allowed = True
                elif parsed_message.message_type == ParsedMessageType.PAUSE_PUBLICATION:
                    self._input_allowed = False

            # Handle sequence tracking
            client_message = parsed_message.client_message
            if client_message:
                # Schedule acknowledgment for applicable messages
                if client_message.message_type not in (
                    MESSAGE_ACKNOWLEDGE,
                    MESSAGE_CHANNEL_CLOSED,
                    MESSAGE_START_PUBLICATION,
                    MESSAGE_PAUSE_PUBLICATION,
                ):
                    self._schedule_acknowledgment(client_message)

                # Update expected sequence for output stream messages
                if client_message.message_type == MESSAGE_OUTPUT_STREAM:
                    if new_seq is not None:
                        # In-order message processed
                        self._expected_sequence_number = new_seq
                        self.logger.debug(
                            f"Updated expected sequence to {self._expected_sequence_number}"
                        )
                        self._drain_buffered_output()
                    elif (
                        client_message.sequence_number > self._expected_sequence_number
                    ):
                        # Out-of-order message; buffer it
                        if parsed_message.raw_data:
                            self._incoming_buffer[client_message.sequence_number] = (
                                parsed_message.raw_data
                            )
                        self.logger.debug(
                            f"Buffered future sequence {client_message.sequence_number}, expected {self._expected_sequence_number}"
                        )

        except Exception as e:
            self.logger.error(f"Error processing parsed message: {e}")

    async def _async_send_message(self, message_data: Any) -> None:
        """Async wrapper for sending messages."""
        if self._channel is not None:
            await self._channel.send_message(message_data)

    def _drain_buffered_output(self) -> None:
        """Process buffered output messages in order starting from expected sequence."""
        try:
            from .protocol import parse_client_message

            while self._expected_sequence_number in self._incoming_buffer:
                raw = self._incoming_buffer.pop(self._expected_sequence_number)
                cm = parse_client_message(raw)
                if cm and cm.is_shell_output():
                    shell_data = cm.get_shell_data()
                    if shell_data and self._input_handler:
                        self._input_handler(shell_data.encode("utf-8"))
                    # Route to per-stream handlers for buffered messages too
                    try:
                        if cm.payload_type == PayloadType.OUTPUT:
                            if self._stdout_handler:
                                self._stdout_handler(shell_data.encode("utf-8"))
                        elif cm.payload_type == PayloadType.STDERR:
                            if self._stderr_handler:
                                self._stderr_handler(shell_data.encode("utf-8"))
                    except Exception:
                        pass
                self._expected_sequence_number += 1
                self.logger.debug(
                    f"Drained buffered seq; expected now {self._expected_sequence_number}"
                )
        except Exception as e:
            self.logger.debug(f"Drain buffer failed: {e}")

    def _handle_error(self, error: Exception) -> None:
        """Handle WebSocket errors."""
        self.logger.error(f"Data channel error: {error}")

    def _handle_connection_change(self, state: ConnectionState) -> None:
        """Handle connection state changes."""
        self.logger.debug(f"Data channel connection state: {state.value}")
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

    def get_channel_info(self) -> dict[str, Any]:
        """Get channel information."""
        if self._channel:
            info = self._channel.get_connection_info()
            info["expected_sequence_number"] = self._expected_sequence_number
            return info
        else:
            return {
                "state": "not_initialized",
                "is_open": False,
                "expected_sequence_number": self._expected_sequence_number,
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
            if self._channel is not None:
                await self._channel.send_message(message_json)

            self.logger.debug(
                f"Sent handshake initialization: RequestId={handshake_message['RequestId']}"
            )

        except Exception as e:
            self.logger.error(f"Failed to send handshake initialization: {e}")
            raise

    def _schedule_acknowledgment(self, original_message: Any) -> None:
        """Schedule acknowledgment message to be sent asynchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g., synchronous unit test); skip scheduling to avoid un-awaited coroutine warnings
            self.logger.debug("No running event loop; skipping async ack scheduling")
            return
        # Schedule the acknowledgment to be sent in the next event loop iteration
        loop.create_task(self._send_acknowledgment(original_message))

    async def _send_acknowledgment(self, original_message: Any) -> None:
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

    def _serialize_input_message_with_payload_type(
        self, input_data: bytes, payload_type: int
    ) -> bytes:
        """Serialize input message with specific payload type."""
        # Note: line ending normalization handled earlier
        message_uuid = uuid.uuid4()
        created_date = int(time.time() * 1000)

        # Capture current outbound sequence and then advance
        seq = self._out_seq
        self._out_seq += 1

        return serialize_client_message(
            message_type=MESSAGE_INPUT_STREAM,
            schema_version=1,
            created_date=created_date,
            sequence_number=seq,
            flags=0,
            message_id=message_uuid.bytes,
            payload=input_data,
            payload_type=payload_type,
        )

    def _schedule_shell_input(self, data: bytes) -> None:
        """Deprecated: previously used for experimental auto-input; now a no-op."""
        self.logger.debug("_schedule_shell_input is deprecated and will be ignored.")

    def _create_input_stream_message(self, input_data: bytes) -> bytes:
        """Create AWS SSM input stream message for keyboard input."""
        # Use OUTPUT payload type for normal keyboard input (matches Go plugin)
        return self._serialize_input_message_with_payload_type(
            input_data, PayloadType.OUTPUT
        )

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
            msg = self._serialize_input_message_with_payload_type(
                payload, PayloadType.SIZE
            )
            await self._channel.send_message(msg)
            self.logger.debug(f"Sent terminal size: cols={cols}, rows={rows}")
        except Exception as e:
            self.logger.error(f"Failed to send terminal size: {e}")
