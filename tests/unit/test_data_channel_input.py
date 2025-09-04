"""Unit tests for SessionDataChannel input behavior.

These tests verify:
- Outbound input messages use PayloadType.OUTPUT and incrementing sequence numbers (starting at 0)
- Handshake MessageSchemaVersion is numeric (1)
- Line ending normalization maps LF/CRLF to CR
"""

from __future__ import annotations

import json
from typing import Any, List

import pytest

from pyssm_client.communicator.data_channel import SessionDataChannel
from pyssm_client.communicator.types import WebSocketConfig
from pyssm_client.communicator.protocol import (
    parse_client_message,
    PayloadType,
)


class DummyChannel:
    """Minimal stub channel capturing sent messages."""

    def __init__(self) -> None:
        self.sent: List[Any] = []
        self.is_connected = True

    async def send_message(self, data: Any) -> None:  # type: ignore[override]
        self.sent.append(data)

    def get_connection_info(self) -> dict:
        return {"state": "connected", "is_connected": True, "url": "wss://example"}


@pytest.mark.asyncio
async def test_input_payload_type_and_sequence() -> None:
    cfg = WebSocketConfig(url="wss://example/stream", token="T")
    dc = SessionDataChannel(cfg)

    # Inject dummy channel (pretend it's open)
    dummy = DummyChannel()
    dc._channel = dummy  # type: ignore[attr-defined]

    # Send two keystrokes
    await dc.send_input_data(b"a")
    await dc.send_input_data(b"\n")  # Enter

    # Two binary frames should have been sent
    assert len(dummy.sent) == 2

    m1 = parse_client_message(dummy.sent[0])
    m2 = parse_client_message(dummy.sent[1])

    assert m1 is not None and m2 is not None
    assert m1.message_type.strip() == "input_stream_data"
    assert m2.message_type.strip() == "input_stream_data"

    # PayloadType for keyboard input must be OUTPUT
    assert m1.payload_type == PayloadType.OUTPUT
    assert m2.payload_type == PayloadType.OUTPUT

    # Sequence numbers must increment starting from 0
    assert m1.sequence_number == 0
    assert m2.sequence_number == 1


@pytest.mark.asyncio
async def test_handshake_schema_version_numeric() -> None:
    cfg = WebSocketConfig(url="wss://example/stream", token="T")
    dc = SessionDataChannel(cfg)
    dummy = DummyChannel()
    dc._channel = dummy  # type: ignore[attr-defined]

    # Call handshake directly
    await dc._send_handshake_initialization()  # type: ignore[attr-defined]

    assert len(dummy.sent) == 1
    payload = dummy.sent[0]
    assert isinstance(payload, str)
    obj = json.loads(payload)
    # MessageSchemaVersion should be an integer 1
    assert isinstance(obj.get("MessageSchemaVersion"), int)
    assert obj.get("MessageSchemaVersion") == 1


@pytest.mark.asyncio
async def test_line_ending_normalization() -> None:
    cfg = WebSocketConfig(url="wss://example/stream", token="T")
    dc = SessionDataChannel(cfg)
    dummy = DummyChannel()
    dc._channel = dummy  # type: ignore[attr-defined]

    # Send CRLF and LF; both should become CR in payload
    await dc.send_input_data(b"echo test\r\n")
    await dc.send_input_data(b"echo ok\n")

    assert len(dummy.sent) == 2
    m1 = parse_client_message(dummy.sent[0])
    m2 = parse_client_message(dummy.sent[1])
    assert m1 is not None and m2 is not None

    assert m1.payload.endswith(b"\r")
    assert not m1.payload.endswith(b"\r\n")
    assert m2.payload.endswith(b"\r")
