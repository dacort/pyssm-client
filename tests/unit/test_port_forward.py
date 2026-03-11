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


# --- PortForwardBridge tests ---


class TestPortForwardBridge:
    def _make_mock_data_channel(self) -> MagicMock:
        dc = MagicMock()
        dc.is_open = True
        dc.set_input_handler = MagicMock()
        dc.set_parameter_handler = MagicMock()
        dc.set_flag_handler = MagicMock()
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
            # Handlers should be wired up
            dc.set_input_handler.assert_called_once()
            dc.set_parameter_handler.assert_called_once()
            dc.set_flag_handler.assert_called_once()
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_parameter_handshake(self) -> None:
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        assert not bridge._handshake_done.is_set()

        bridge._on_parameter(
            json.dumps({"portNumber": "8080"}).encode(),
            {"portNumber": "8080"},
        )

        assert bridge._handshake_done.is_set()
        # Should have scheduled ack send
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_flag_connect_disconnect(self) -> None:
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        # Connect flag
        connect_payload = struct.pack(">I", FLAG_CONNECT_TO_PORT)
        bridge._on_flag(connect_payload, {})
        assert bridge._connection_active.is_set()

        # Disconnect flag
        disconnect_payload = struct.pack(">I", FLAG_DISCONNECT_TO_PORT)
        bridge._on_flag(disconnect_payload, {})
        assert not bridge._connection_active.is_set()

    @pytest.mark.asyncio
    async def test_ssm_data_forwarded_to_tcp(self) -> None:
        """Test that data from SSM is written to the active TCP writer."""
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        # Mock a TCP writer
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        bridge._current_writer = mock_writer

        bridge._on_ssm_data(b"hello from remote")

        mock_writer.write.assert_called_once_with(b"hello from remote")

    @pytest.mark.asyncio
    async def test_ssm_data_dropped_without_writer(self) -> None:
        """Data from SSM is silently dropped when no TCP writer is active."""
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)

        # No writer set - should not raise
        bridge._on_ssm_data(b"dropped data")

    @pytest.mark.asyncio
    async def test_tcp_data_forwarded_to_ssm(self) -> None:
        """Test that TCP connection data is forwarded to the SSM data channel."""
        dc = self._make_mock_data_channel()
        shutdown = asyncio.Event()
        bridge = PortForwardBridge(dc, shutdown)
        bridge._handshake_done.set()

        try:
            port = await bridge.start(0)

            # Connect to the bridge's TCP listener
            reader, writer = await asyncio.open_connection("127.0.0.1", port)

            # Give the connection handler time to start
            await asyncio.sleep(0.05)

            # Send data through TCP
            writer.write(b"hello from local")
            await writer.drain()

            # Give time for forwarding
            await asyncio.sleep(0.1)

            # Verify data was sent to SSM
            dc.send_raw_input_data.assert_called()
            sent_data = b"".join(
                call.args[0] for call in dc.send_raw_input_data.call_args_list
            )
            assert b"hello from local" in sent_data

            writer.close()
            await writer.wait_closed()

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
