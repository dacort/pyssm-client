"""Port forwarding bridge: local TCP listener <-> SSM data channel via smux.

This module implements the client side of the xtaci/smux stream multiplexing
protocol used by SSM agents for port forwarding:

Protocol flow (matches Go reference muxportforwarding.go):
1. SSM handshake completes (HandshakeRequest/Response/Complete).
2. Bridge creates a smux session over the SSM data channel.
3. For each inbound TCP connection the bridge opens a new smux stream (SYN).
4. Data flows bidirectionally: TCP <-> smux stream <-> SSM data channel <-> agent.
5. On TCP disconnect the stream is closed (FIN).
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any, Dict

from ..communicator.data_channel import SessionDataChannel
from ..communicator.smux import SmuxSession
from ..utils.logging import get_logger

# Flag values from the Go reference (used in FLAG payloads).
FLAG_CONNECT_TO_PORT = 1
FLAG_DISCONNECT_TO_PORT = 2
FLAG_CONNECT_TO_PORT_ERROR = 3


class PortForwardBridge:
    """Bridges a local TCP listener with an SSM data channel using smux."""

    def __init__(
        self,
        data_channel: SessionDataChannel,
        shutdown_event: asyncio.Event,
        port_parameters: dict[str, str] | None = None,
    ) -> None:
        self._logger = get_logger(__name__)
        self._data_channel = data_channel
        self._shutdown_event = shutdown_event
        self._port_parameters = port_parameters or {}
        self._server: asyncio.Server | None = None
        self._local_port: int = 0
        # Set once the SSM handshake completes and smux is ready.
        self._handshake_done = asyncio.Event()
        # smux session (created after SSM handshake)
        self._smux: SmuxSession | None = None

    # -- Public API --

    async def start(self, local_port: int) -> int:
        """Start the local TCP listener.

        Returns the actual bound port number.
        """
        # Wire up SSM data channel handlers.
        # Use raw_input_handler to get binary payload without UTF-8 roundtrip.
        self._data_channel.set_raw_input_handler(self._on_ssm_data)
        self._data_channel.set_flag_handler(self._on_flag)
        self._data_channel.set_handshake_complete_handler(self._on_handshake_complete)

        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", local_port
        )

        sockets = self._server.sockets
        if sockets:
            self._local_port = sockets[0].getsockname()[1]
        else:
            self._local_port = local_port

        self._logger.debug(f"TCP listener started on 127.0.0.1:{self._local_port}")
        return self._local_port

    @property
    def local_port(self) -> int:
        return self._local_port

    async def stop(self) -> None:
        """Stop the bridge: close TCP listener, smux, and active connections."""
        if self._smux:
            try:
                await self._smux.close()
            except Exception:
                pass
            self._smux = None

        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

        self._logger.debug("Port forward bridge stopped")

    # -- SSM data channel callbacks --

    def _on_handshake_complete(self) -> None:
        """Called when the SSM handshake finishes.

        Creates the smux session and marks the bridge as ready.
        The Go reference creates the smux session in InitializeStreams()
        which runs after the handshake.
        """
        self._logger.debug("SSM handshake complete, initializing smux session")
        self._smux = SmuxSession(self._send_smux_data)
        if not self._handshake_done.is_set():
            self._handshake_done.set()
        self._logger.debug("smux session ready, accepting TCP connections")

    def _on_ssm_data(self, data: bytes) -> None:
        """Called when OUTPUT data arrives from the SSM agent.

        Feeds raw bytes into the smux session for frame reassembly.
        """
        if self._smux is None:
            self._logger.debug(
                f"SSM data ({len(data)} bytes) before smux init, dropping"
            )
            return
        self._smux.feed_data(data)

    def _on_flag(self, raw: bytes, _parsed: Dict[str, Any]) -> None:
        """Handle FLAG payload from SSM agent."""
        flag_value = 0
        try:
            if len(raw) >= 4:
                flag_value = struct.unpack(">I", raw[:4])[0]
        except Exception:
            pass

        self._logger.debug(f"Port flag received: {flag_value}")
        if flag_value == FLAG_CONNECT_TO_PORT_ERROR:
            self._logger.error("Agent reported: connection to destination port failed")

    # -- smux send helper --

    async def _send_smux_data(self, data: bytes) -> None:
        """Send raw smux frame bytes over the SSM data channel."""
        if not self._data_channel.is_open:
            return
        await self._data_channel.send_raw_input_data(data)

    # -- TCP connection handling --

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle an incoming TCP connection by opening a smux stream."""
        peer = writer.get_extra_info("peername")
        self._logger.debug(f"TCP connection from {peer}")

        # Wait for smux session to be ready
        try:
            await asyncio.wait_for(self._handshake_done.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            self._logger.error("Port forwarding handshake timed out")
            writer.close()
            return

        if self._smux is None:
            self._logger.error("smux session not initialized")
            writer.close()
            return

        # Open a new smux stream for this TCP connection
        stream = await self._smux.open_stream()
        self._logger.debug(f"Opened smux stream {stream.stream_id} for {peer}")

        # Run bidirectional proxy: TCP <-> smux stream
        tcp_to_smux = asyncio.create_task(self._proxy_tcp_to_smux(reader, stream, peer))
        smux_to_tcp = asyncio.create_task(self._proxy_smux_to_tcp(stream, writer, peer))

        try:
            # Wait for either direction to finish
            done, pending = await asyncio.wait(
                [tcp_to_smux, smux_to_tcp],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel the other direction
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        except Exception as e:
            self._logger.error(f"Proxy error for {peer}: {e}")
        finally:
            # Close the smux stream
            try:
                await stream.close()
            except Exception:
                pass
            # Close the TCP connection
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            self._logger.debug(f"Connection from {peer} cleaned up")

    async def _proxy_tcp_to_smux(
        self,
        reader: asyncio.StreamReader,
        stream: Any,
        peer: Any,
    ) -> None:
        """Read from TCP and write to smux stream."""
        try:
            while not self._shutdown_event.is_set():
                data = await reader.read(32768)
                if not data:
                    self._logger.debug(f"TCP EOF from {peer}")
                    break
                await stream.write(data)
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            self._logger.debug(f"TCP reset from {peer}")
        except Exception as e:
            self._logger.debug(f"TCP→smux error: {e}")

    async def _proxy_smux_to_tcp(
        self,
        stream: Any,
        writer: asyncio.StreamWriter,
        peer: Any,
    ) -> None:
        """Read from smux stream and write to TCP."""
        try:
            while not self._shutdown_event.is_set():
                data = await stream.read()
                if not data:
                    self._logger.debug(f"smux EOF for {peer}")
                    break
                if writer.is_closing():
                    break
                writer.write(data)
                await writer.drain()
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            self._logger.debug(f"TCP write reset for {peer}")
        except Exception as e:
            self._logger.debug(f"smux→TCP error: {e}")
