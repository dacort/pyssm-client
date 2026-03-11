"""Unit tests for port forwarding implementation."""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyssm_client.cli.port_forward import (
    FLAG_CONNECT_TO_PORT,
    FLAG_DISCONNECT_TO_PORT,
    FLAG_CONNECT_TO_PORT_ERROR,
    PortForwardBridge,
)
from pyssm_client.cli.types import PortForwardArguments
from pyssm_client.communicator.message_handlers import (
    MessageHandlerContext,
    MessageRouter,
)
from pyssm_client.communicator.message_parser import (
    MessageParser,
    ParsedMessage,
    ParsedMessageType,
)
from pyssm_client.communicator.protocol import serialize_client_message
from pyssm_client.communicator.smux import (
    SmuxSession,
    SmuxStream,
    _pack_header,
    _unpack_header,
    CMD_SYN,
    CMD_FIN,
    CMD_PSH,
    CMD_NOP,
    SMUX_VERSION,
    HEADER_SIZE,
)
from pyssm_client.communicator.types import MessageType, WebSocketMessage
from pyssm_client.constants import MESSAGE_OUTPUT_STREAM, PayloadType


# --- PortForwardArguments tests ---


class TestPortForwardArguments:
    def test_valid_args(self) -> None:
        args = PortForwardArguments(target="i-1234567890abcdef0", remote_port=8080)
        assert args.validate() == []

    def test_valid_with_local_port(self) -> None:
        args = PortForwardArguments(target="i-abc", remote_port=3306, local_port=13306)
        assert args.validate() == []

    def test_valid_with_remote_host(self) -> None:
        args = PortForwardArguments(
            target="i-abc",
            remote_port=3306,
            remote_host="mydb.xxx.rds.amazonaws.com",
        )
        assert args.validate() == []

    def test_missing_target(self) -> None:
        args = PortForwardArguments(target="", remote_port=8080)
        errors = args.validate()
        assert any("target is required" in e for e in errors)

    def test_invalid_target_format(self) -> None:
        args = PortForwardArguments(target="ec2-instance", remote_port=8080)
        errors = args.validate()
        assert any("instance ID" in e for e in errors)

    def test_valid_target_prefixes(self) -> None:
        for prefix in ["i-abc", "mi-abc", "ssm-abc"]:
            args = PortForwardArguments(target=prefix, remote_port=80)
            assert args.validate() == [], f"Failed for target {prefix}"

    def test_invalid_remote_port_zero(self) -> None:
        args = PortForwardArguments(target="i-abc", remote_port=0)
        errors = args.validate()
        assert any("remote_port" in e for e in errors)

    def test_invalid_remote_port_too_high(self) -> None:
        args = PortForwardArguments(target="i-abc", remote_port=70000)
        errors = args.validate()
        assert any("remote_port" in e for e in errors)

    def test_valid_remote_port_boundaries(self) -> None:
        for port in [1, 80, 443, 8080, 65535]:
            args = PortForwardArguments(target="i-abc", remote_port=port)
            assert args.validate() == [], f"Failed for port {port}"

    def test_invalid_local_port(self) -> None:
        args = PortForwardArguments(target="i-abc", remote_port=80, local_port=70000)
        errors = args.validate()
        assert any("local_port" in e for e in errors)

    def test_local_port_zero_is_auto(self) -> None:
        args = PortForwardArguments(target="i-abc", remote_port=80, local_port=0)
        assert args.validate() == []

    def test_aws_config_fields(self) -> None:
        args = PortForwardArguments(
            target="i-abc",
            remote_port=80,
            profile="dev",
            region="us-west-2",
            endpoint_url="https://custom.endpoint",
        )
        assert args.profile == "dev"
        assert args.region == "us-west-2"
        assert args.endpoint_url == "https://custom.endpoint"
        assert args.validate() == []


# --- MessageParser tests for PORT_PARAMETER and PORT_FLAG ---


class TestMessageParserPortTypes:
    def _make_binary_message(
        self, payload_type: int, payload: bytes
    ) -> WebSocketMessage:
        """Create a binary WebSocket message with given payload type."""
        import uuid

        raw = serialize_client_message(
            message_type=MESSAGE_OUTPUT_STREAM,
            schema_version=1,
            created_date=1000,
            sequence_number=0,
            flags=0,
            message_id=uuid.uuid4().bytes,
            payload=payload,
            payload_type=payload_type,
        )
        return WebSocketMessage(message_type=MessageType.BINARY, data=raw)

    def test_parameter_message_parsed(self) -> None:
        parser = MessageParser()
        payload = json.dumps({"portNumber": "8080"}).encode("utf-8")
        msg = self._make_binary_message(PayloadType.PARAMETER, payload)

        result = parser.parse_websocket_message(msg)
        assert result is not None
        assert result.message_type == ParsedMessageType.PORT_PARAMETER
        assert result.parsed_payload.get("portNumber") == "8080"

    def test_flag_message_parsed(self) -> None:
        parser = MessageParser()
        # FLAG payload: 4-byte big-endian int
        payload = struct.pack(">I", FLAG_CONNECT_TO_PORT)
        msg = self._make_binary_message(PayloadType.FLAG, payload)

        result = parser.parse_websocket_message(msg)
        assert result is not None
        assert result.message_type == ParsedMessageType.PORT_FLAG

    def test_output_still_parsed_as_shell(self) -> None:
        parser = MessageParser()
        payload = b"hello world"
        msg = self._make_binary_message(PayloadType.OUTPUT, payload)

        result = parser.parse_websocket_message(msg)
        assert result is not None
        assert result.message_type == ParsedMessageType.SHELL_OUTPUT


# --- MessageRouter tests for PORT_PARAMETER and PORT_FLAG ---


class TestMessageRouterPortTypes:
    @pytest.mark.asyncio
    async def test_parameter_routed_to_handler(self) -> None:
        router = MessageRouter()
        received: List[tuple[bytes, Dict[str, Any]]] = []

        def handler(raw: bytes, parsed: Dict[str, Any]) -> None:
            received.append((raw, parsed))

        context = MessageHandlerContext(
            send_message=AsyncMock(),
            trigger_closed=MagicMock(),
            parameter_handler=handler,
        )

        payload_data = {"portNumber": "3306"}
        message = ParsedMessage(
            message_type=ParsedMessageType.PORT_PARAMETER,
            client_message=MagicMock(
                payload=json.dumps(payload_data).encode(),
                message_type="output_stream_data",
            ),
            parsed_payload=payload_data,
        )

        processed, new_seq, input_change = await router.route_message(
            message, context, 0, MagicMock()
        )

        assert processed is True
        assert len(received) == 1
        assert received[0][1]["portNumber"] == "3306"

    @pytest.mark.asyncio
    async def test_flag_routed_to_handler(self) -> None:
        router = MessageRouter()
        received: List[tuple[bytes, Dict[str, Any]]] = []

        def handler(raw: bytes, parsed: Dict[str, Any]) -> None:
            received.append((raw, parsed))

        context = MessageHandlerContext(
            send_message=AsyncMock(),
            trigger_closed=MagicMock(),
            flag_handler=handler,
        )

        flag_payload = struct.pack(">I", FLAG_CONNECT_TO_PORT)
        message = ParsedMessage(
            message_type=ParsedMessageType.PORT_FLAG,
            client_message=MagicMock(
                payload=flag_payload,
                message_type="output_stream_data",
            ),
            parsed_payload={},
        )

        processed, _, _ = await router.route_message(message, context, 0, MagicMock())

        assert processed is True
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_parameter_no_handler_still_processed(self) -> None:
        router = MessageRouter()
        context = MessageHandlerContext(
            send_message=AsyncMock(),
            trigger_closed=MagicMock(),
        )

        message = ParsedMessage(
            message_type=ParsedMessageType.PORT_PARAMETER,
            client_message=MagicMock(payload=b"{}"),
            parsed_payload={},
        )

        processed, _, _ = await router.route_message(message, context, 0, MagicMock())

        assert processed is True


# --- SmuxSession tests ---


class TestSmuxFraming:
    def test_pack_unpack_header(self) -> None:
        header = _pack_header(CMD_PSH, 3, 100)
        assert len(header) == HEADER_SIZE
        ver, cmd, length, sid = _unpack_header(header)
        assert ver == SMUX_VERSION
        assert cmd == CMD_PSH
        assert length == 100
        assert sid == 3

    def test_syn_header(self) -> None:
        header = _pack_header(CMD_SYN, 5, 0)
        ver, cmd, length, sid = _unpack_header(header)
        assert cmd == CMD_SYN
        assert length == 0
        assert sid == 5

    def test_fin_header(self) -> None:
        header = _pack_header(CMD_FIN, 3, 0)
        ver, cmd, length, sid = _unpack_header(header)
        assert cmd == CMD_FIN
        assert length == 0


class TestSmuxSession:
    @pytest.mark.asyncio
    async def test_open_stream_sends_syn(self) -> None:
        sent: list[bytes] = []

        async def send(data: bytes) -> None:
            sent.append(data)

        session = SmuxSession(send)
        stream = await session.open_stream()

        assert stream.stream_id == 3  # first client stream
        assert len(sent) == 1
        ver, cmd, length, sid = _unpack_header(sent[0])
        assert cmd == CMD_SYN
        assert sid == 3
        assert length == 0

    @pytest.mark.asyncio
    async def test_stream_write_sends_psh(self) -> None:
        sent: list[bytes] = []

        async def send(data: bytes) -> None:
            sent.append(data)

        session = SmuxSession(send)
        stream = await session.open_stream()
        sent.clear()  # ignore SYN

        await stream.write(b"hello")

        assert len(sent) == 1
        ver, cmd, length, sid = _unpack_header(sent[0][:HEADER_SIZE])
        assert cmd == CMD_PSH
        assert length == 5
        assert sid == 3
        assert sent[0][HEADER_SIZE:] == b"hello"

    @pytest.mark.asyncio
    async def test_stream_close_sends_fin(self) -> None:
        sent: list[bytes] = []

        async def send(data: bytes) -> None:
            sent.append(data)

        session = SmuxSession(send)
        stream = await session.open_stream()
        sent.clear()

        await stream.close()

        assert len(sent) == 1
        ver, cmd, length, sid = _unpack_header(sent[0])
        assert cmd == CMD_FIN
        assert sid == 3

    @pytest.mark.asyncio
    async def test_feed_data_psh(self) -> None:
        """Feeding a PSH frame makes data available on the stream."""

        async def noop(data: bytes) -> None:
            pass

        session = SmuxSession(noop)
        stream = await session.open_stream()

        # Feed a PSH frame for stream 3
        frame = _pack_header(CMD_PSH, 3, 5) + b"world"
        session.feed_data(frame)

        data = await asyncio.wait_for(stream.read(), timeout=1.0)
        assert data == b"world"

    @pytest.mark.asyncio
    async def test_feed_data_fin(self) -> None:
        """Feeding a FIN frame causes read() to return empty bytes."""

        async def noop(data: bytes) -> None:
            pass

        session = SmuxSession(noop)
        stream = await session.open_stream()

        frame = _pack_header(CMD_FIN, 3, 0)
        session.feed_data(frame)

        data = await asyncio.wait_for(stream.read(), timeout=1.0)
        assert data == b""

    @pytest.mark.asyncio
    async def test_feed_partial_frame(self) -> None:
        """Partial frames are buffered until complete."""

        async def noop(data: bytes) -> None:
            pass

        session = SmuxSession(noop)
        stream = await session.open_stream()

        full_frame = _pack_header(CMD_PSH, 3, 5) + b"hello"
        # Feed header only
        session.feed_data(full_frame[:HEADER_SIZE])
        assert stream._recv_buf.empty()

        # Feed the rest
        session.feed_data(full_frame[HEADER_SIZE:])
        data = await asyncio.wait_for(stream.read(), timeout=1.0)
        assert data == b"hello"

    @pytest.mark.asyncio
    async def test_multiple_streams(self) -> None:
        sent: list[bytes] = []

        async def send(data: bytes) -> None:
            sent.append(data)

        session = SmuxSession(send)
        s1 = await session.open_stream()
        s2 = await session.open_stream()

        assert s1.stream_id == 3
        assert s2.stream_id == 5

    @pytest.mark.asyncio
    async def test_nop_ignored(self) -> None:
        async def noop(data: bytes) -> None:
            pass

        session = SmuxSession(noop)
        stream = await session.open_stream()

        # NOP frame
        frame = _pack_header(CMD_NOP, 0, 0)
        session.feed_data(frame)
        # Stream should have no data
        assert stream._recv_buf.empty()

    @pytest.mark.asyncio
    async def test_binary_data_integrity(self) -> None:
        """Binary data with non-UTF-8 bytes survives the smux roundtrip."""
        sent: list[bytes] = []

        async def send(data: bytes) -> None:
            sent.append(data)

        session = SmuxSession(send)
        stream = await session.open_stream()

        # Binary data that would be corrupted by UTF-8 decode/encode
        binary_data = bytes(range(256))
        frame = _pack_header(CMD_PSH, 3, 256) + binary_data
        session.feed_data(frame)

        data = await asyncio.wait_for(stream.read(), timeout=1.0)
        assert data == binary_data


# --- PortForwardBridge tests ---


class TestPortForwardBridge:
    def _make_mock_data_channel(self) -> MagicMock:
        dc = MagicMock()
        dc.is_open = True
        dc.set_raw_input_handler = MagicMock()
        dc.set_input_handler = MagicMock()
        dc.set_parameter_handler = MagicMock()
        dc.set_flag_handler = MagicMock()
        dc.set_handshake_complete_handler = MagicMock()
        dc.send_raw_input_data = AsyncMock()
        dc._serialize_input_message_with_payload_type = MagicMock(return_value=b"ack")
        dc._async_send_message = AsyncMock()
        return dc

    @pytest.mark.asyncio
    async def test_start_binds_port(self) -> None:
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        try:
            port = await bridge.start(0)
            assert port > 0
            assert bridge.local_port == port
            # Raw handler and flag handler should be wired up
            dc.set_raw_input_handler.assert_called_once()
            dc.set_flag_handler.assert_called_once()
            dc.set_handshake_complete_handler.assert_called_once()
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_handshake_complete_initializes_smux(self) -> None:
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        assert not bridge._handshake_done.is_set()
        assert bridge._smux is None

        bridge._on_handshake_complete()

        assert bridge._handshake_done.is_set()
        assert bridge._smux is not None

    @pytest.mark.asyncio
    async def test_flag_connect_error_logged(self) -> None:
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        # Connect error flag should not crash
        error_payload = struct.pack(">I", FLAG_CONNECT_TO_PORT_ERROR)
        bridge._on_flag(error_payload, {})

    @pytest.mark.asyncio
    async def test_ssm_data_feeds_smux(self) -> None:
        """Data from SSM is fed into the smux session."""
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        # Initialize smux
        bridge._on_handshake_complete()

        # Feed a smux PSH frame for stream 3 (would need a stream first)
        # Just verify no crash when feeding data before any streams exist
        frame = _pack_header(CMD_PSH, 99, 5) + b"hello"
        bridge._on_ssm_data(frame)  # unknown stream, should not crash

    @pytest.mark.asyncio
    async def test_ssm_data_before_smux_dropped(self) -> None:
        """Data arriving before smux init is dropped."""
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        # No handshake yet, smux is None
        bridge._on_ssm_data(b"too early")  # should not crash

    @pytest.mark.asyncio
    async def test_tcp_data_forwarded_via_smux(self) -> None:
        """TCP data is wrapped in smux PSH frames and sent to SSM."""
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        # Initialize smux and mark ready
        bridge._on_handshake_complete()

        try:
            port = await bridge.start(0)

            # Connect TCP client
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.sleep(0.1)

            # Send data through TCP
            writer.write(b"hello from local")
            await writer.drain()
            await asyncio.sleep(0.1)

            # Verify smux-framed data was sent via SSM
            dc.send_raw_input_data.assert_called()
            all_sent = b"".join(
                call.args[0] for call in dc.send_raw_input_data.call_args_list
            )
            # First call should be SYN (8 bytes), then PSH with our data
            # Check that our data appears in a PSH frame
            assert b"hello from local" in all_sent

            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.05)

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self) -> None:
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        await bridge.start(0)
        assert bridge._server is not None

        await bridge.stop()
        assert bridge._server is None
