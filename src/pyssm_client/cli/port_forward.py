"""Port forwarding bridge: local TCP listener <-> SSM data channel."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from ..communicator.data_channel import SessionDataChannel
from ..constants import PayloadType
from ..utils.logging import get_logger


# Flag values sent by the SSM agent for port forwarding connection lifecycle.
# These match the Go reference implementation constants.
FLAG_CONNECT_TO_PORT = 1
FLAG_DISCONNECT_TO_PORT = 2


class PortForwardBridge:
    """Bridges a local TCP listener with an SSM data channel for port forwarding."""

    def __init__(
        self,
        data_channel: SessionDataChannel,
        shutdown_event: asyncio.Event,
    ) -> None:
        self._logger = get_logger(__name__)
        self._data_channel = data_channel
        self._shutdown_event = shutdown_event
        self._server: asyncio.Server | None = None
        self._local_port: int = 0
        self._current_writer: asyncio.StreamWriter | None = None
        self._current_reader_task: asyncio.Task[None] | None = None
        self._handshake_done = asyncio.Event()
        self._connection_active = asyncio.Event()

    async def start(self, local_port: int) -> int:
        """Start the local TCP listener.

        Args:
            local_port: Port to listen on (0 for auto-assign).

        Returns:
            The actual bound port number.
        """
        # Wire up SSM data channel handlers
        self._data_channel.set_input_handler(self._on_ssm_data)
        self._data_channel.set_parameter_handler(self._on_parameter)
        self._data_channel.set_flag_handler(self._on_flag)

        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", local_port
        )

        # Determine actual port
        sockets = self._server.sockets
        if sockets:
            addr = sockets[0].getsockname()
            self._local_port = addr[1]
        else:
            self._local_port = local_port

        self._logger.debug(f"TCP listener started on 127.0.0.1:{self._local_port}")
        return self._local_port

    @property
    def local_port(self) -> int:
        return self._local_port

    # -- SSM data channel callbacks --

    def _on_ssm_data(self, data: bytes) -> None:
        """Called when data arrives from the SSM agent (remote port)."""
        writer = self._current_writer
        if writer is None or writer.is_closing():
            self._logger.debug("SSM data received but no active TCP connection")
            return
        try:
            writer.write(data)
            # Schedule drain without blocking the sync callback
            asyncio.ensure_future(self._drain_writer(writer))
        except Exception as e:
            self._logger.debug(f"Failed to write SSM data to TCP: {e}")

    async def _drain_writer(self, writer: asyncio.StreamWriter) -> None:
        try:
            await writer.drain()
        except Exception:
            pass

    def _on_parameter(self, raw: bytes, parsed: Dict[str, Any]) -> None:
        """Handle PARAMETER payload from SSM agent (port forwarding handshake).

        The agent sends port parameters; the client echoes them back as acknowledgment.
        """
        self._logger.debug(f"Port parameter received: {parsed}")

        # Echo back the parameters as acknowledgment
        try:
            ack_payload = raw  # Echo raw payload bytes
            asyncio.ensure_future(self._send_parameter_ack(ack_payload))
        except Exception as e:
            self._logger.error(f"Failed to send parameter ack: {e}")

        self._handshake_done.set()
        self._logger.debug("Port forwarding handshake complete")

    async def _send_parameter_ack(self, payload: bytes) -> None:
        """Send parameter acknowledgment back to SSM agent."""
        if not self._data_channel.is_open:
            return
        msg = self._data_channel._serialize_input_message_with_payload_type(
            payload, PayloadType.PARAMETER
        )
        await self._data_channel._async_send_message(msg)
        self._logger.debug("Sent parameter acknowledgment")

    def _on_flag(self, raw: bytes, parsed: Dict[str, Any]) -> None:
        """Handle FLAG payload from SSM agent (connection lifecycle)."""
        # Try to extract the flag value
        # The Go implementation sends the flag as a single int32 in the payload
        flag_value = 0
        try:
            if len(raw) >= 4:
                import struct

                flag_value = struct.unpack(">I", raw[:4])[0]
            elif parsed:
                flag_value = int(parsed.get("Value", parsed.get("value", 0)))
        except Exception:
            pass

        self._logger.debug(f"Port flag received: {flag_value}")

        if flag_value == FLAG_CONNECT_TO_PORT:
            self._connection_active.set()
            self._logger.debug("Remote port connection established")
        elif flag_value == FLAG_DISCONNECT_TO_PORT:
            self._connection_active.clear()
            self._logger.debug("Remote port connection disconnected")
            self._close_current_tcp()

    def _close_current_tcp(self) -> None:
        """Close the current TCP connection if active."""
        writer = self._current_writer
        if writer and not writer.is_closing():
            writer.close()
        self._current_writer = None
        if self._current_reader_task and not self._current_reader_task.done():
            self._current_reader_task.cancel()
            self._current_reader_task = None

    # -- TCP connection handling --

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle an incoming TCP connection."""
        peer = writer.get_extra_info("peername")
        self._logger.debug(f"TCP connection from {peer}")

        # Wait for port forwarding handshake before accepting data
        try:
            await asyncio.wait_for(self._handshake_done.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            self._logger.error("Port forwarding handshake timed out")
            writer.close()
            return

        # Close any previous connection
        self._close_current_tcp()

        self._current_writer = writer

        # Read from TCP and forward to SSM
        try:
            while not self._shutdown_event.is_set():
                data = await reader.read(16384)
                if not data:
                    # EOF from TCP client
                    self._logger.debug(f"TCP connection closed by {peer}")
                    break

                if not self._data_channel.is_open:
                    self._logger.debug("Data channel closed; dropping TCP data")
                    break

                await self._data_channel.send_raw_input_data(data)

        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            self._logger.debug(f"TCP connection reset by {peer}")
        except Exception as e:
            self._logger.error(f"Error reading from TCP: {e}")
        finally:
            self._current_writer = None
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            self._logger.debug(f"TCP connection from {peer} cleaned up")

    # -- Lifecycle --

    async def stop(self) -> None:
        """Stop the bridge: close TCP listener and active connections."""
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

        self._close_current_tcp()
        self._logger.debug("Port forward bridge stopped")
