"""Minimal smux v1 protocol implementation for SSM port forwarding.

Implements just enough of the xtaci/smux protocol to support
multiplexed port forwarding over an SSM data channel.

Frame format (8 bytes header, little-endian):
    version  (1 byte)  — always 1
    cmd      (1 byte)  — SYN=0, FIN=1, PSH=2, NOP=3
    length   (2 bytes) — payload length (LE)
    streamID (4 bytes) — stream identifier (LE)
"""

from __future__ import annotations

import asyncio
import struct
from typing import Callable, Awaitable

from ..utils.logging import get_logger

# smux v1 constants
SMUX_VERSION = 1
CMD_SYN = 0
CMD_FIN = 1
CMD_PSH = 2
CMD_NOP = 3

HEADER_SIZE = 8
MAX_FRAME_SIZE = 32768  # default smux MaxFrameSize

_HEADER_FMT = "<BBhI"  # ver(u8), cmd(u8), length(i16→u16), streamID(u32)
# Actually smux uses uint16 for length, but struct doesn't have unsigned short
# with little-endian. Use "<BBHI" for unsigned.
_HEADER_FMT = "<BBHI"


def _pack_header(cmd: int, stream_id: int, length: int) -> bytes:
    return struct.pack(_HEADER_FMT, SMUX_VERSION, cmd, length, stream_id)


def _unpack_header(data: bytes) -> tuple[int, int, int, int]:
    """Returns (version, cmd, length, stream_id)."""
    return struct.unpack(_HEADER_FMT, data)  # type: ignore[return-value]


class SmuxStream:
    """A single multiplexed stream within a smux session."""

    def __init__(self, stream_id: int, session: SmuxSession) -> None:
        self.stream_id = stream_id
        self._session = session
        self._recv_buf = asyncio.Queue[bytes]()
        self._closed = False
        self._remote_fin = asyncio.Event()

    async def write(self, data: bytes) -> None:
        """Send data on this stream (as one or more PSH frames)."""
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + MAX_FRAME_SIZE]
            frame = _pack_header(CMD_PSH, self.stream_id, len(chunk)) + chunk
            await self._session._send_raw(frame)
            offset += len(chunk)

    async def read(self) -> bytes:
        """Read the next chunk of data from this stream.

        Returns empty bytes on FIN/close.
        """
        if self._closed and self._recv_buf.empty():
            return b""
        try:
            return await self._recv_buf.get()
        except asyncio.CancelledError:
            return b""

    def feed(self, data: bytes) -> None:
        """Feed received PSH data into this stream's buffer."""
        if not self._closed:
            self._recv_buf.put_nowait(data)

    def fin(self) -> None:
        """Handle remote FIN — signal EOF to readers."""
        self._closed = True
        self._remote_fin.set()
        # Push empty sentinel so readers unblock
        try:
            self._recv_buf.put_nowait(b"")
        except asyncio.QueueFull:
            pass

    async def close(self) -> None:
        """Send FIN and mark stream as closed."""
        if not self._closed:
            self._closed = True
            frame = _pack_header(CMD_FIN, self.stream_id, 0)
            await self._session._send_raw(frame)


class SmuxSession:
    """Minimal smux v1 client session over an SSM data channel.

    Instead of wrapping an io.ReadWriteCloser, this session sends/receives
    raw bytes via async callbacks connected to the SSM data channel.
    """

    def __init__(
        self,
        send_fn: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """
        Args:
            send_fn: async callable that sends raw bytes to the SSM data channel.
        """
        self._logger = get_logger(__name__)
        self._send_fn = send_fn
        # Client stream IDs: start at 1, increment by 2 (first OpenStream → 3)
        self._next_stream_id = 1
        self._streams: dict[int, SmuxStream] = {}
        # Incoming byte buffer for frame reassembly
        self._recv_buf = bytearray()
        self._closed = False

    async def open_stream(self) -> SmuxStream:
        """Open a new multiplexed stream (sends SYN frame)."""
        self._next_stream_id += 2
        sid = self._next_stream_id
        stream = SmuxStream(sid, self)
        self._streams[sid] = stream
        # Send SYN
        frame = _pack_header(CMD_SYN, sid, 0)
        await self._send_raw(frame)
        self._logger.debug(f"smux: opened stream {sid}")
        return stream

    def feed_data(self, data: bytes) -> None:
        """Feed raw bytes received from the SSM data channel.

        Called by the port forwarding bridge when output_stream_data arrives.
        Parses smux frames and dispatches to the appropriate stream.
        """
        self._recv_buf.extend(data)
        self._process_buffer()

    def _process_buffer(self) -> None:
        """Parse complete frames from the receive buffer."""
        while len(self._recv_buf) >= HEADER_SIZE:
            ver, cmd, length, sid = _unpack_header(bytes(self._recv_buf[:HEADER_SIZE]))
            if ver != SMUX_VERSION:
                self._logger.error(f"smux: unexpected version {ver}, dropping buffer")
                self._recv_buf.clear()
                return

            total = HEADER_SIZE + length
            if len(self._recv_buf) < total:
                # Incomplete frame, wait for more data
                break

            payload = bytes(self._recv_buf[HEADER_SIZE:total])
            del self._recv_buf[:total]

            self._handle_frame(cmd, sid, payload)

    def _handle_frame(self, cmd: int, sid: int, payload: bytes) -> None:
        """Handle a single decoded smux frame."""
        if cmd == CMD_NOP:
            self._logger.debug("smux: received NOP")
            return

        if cmd == CMD_SYN:
            # Server opened a stream to us (unusual for port forwarding but handle it)
            if sid not in self._streams:
                stream = SmuxStream(sid, self)
                self._streams[sid] = stream
                self._logger.debug(f"smux: accepted stream {sid} from server")
            return

        if sid not in self._streams:
            self._logger.debug(f"smux: frame for unknown stream {sid}, cmd={cmd}")
            return
        stream = self._streams[sid]

        if cmd == CMD_PSH:
            stream.feed(payload)
        elif cmd == CMD_FIN:
            self._logger.debug(f"smux: FIN for stream {sid}")
            stream.fin()
        else:
            self._logger.debug(f"smux: unknown cmd {cmd} for stream {sid}")

    async def _send_raw(self, data: bytes) -> None:
        """Send raw smux frame bytes via the SSM data channel."""
        if not self._closed:
            await self._send_fn(data)

    async def close(self) -> None:
        """Close all streams and the session."""
        self._closed = True
        for stream in list(self._streams.values()):
            try:
                await stream.close()
            except Exception:
                pass
        self._streams.clear()
