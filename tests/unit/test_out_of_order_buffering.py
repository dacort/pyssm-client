from __future__ import annotations

import asyncio
import time
import uuid
import pytest

from pyssm_client.communicator.data_channel import SessionDataChannel
from pyssm_client.communicator.types import (
    WebSocketConfig,
    WebSocketMessage,
    MessageType,
)
from pyssm_client.communicator.protocol import serialize_client_message
from pyssm_client.constants import MESSAGE_OUTPUT_STREAM, PayloadType


def _build_output(seq: int, text: str) -> bytes:
    mid = uuid.uuid4().bytes
    created = int(time.time() * 1000)
    payload = text.encode("utf-8")
    return serialize_client_message(
        message_type=MESSAGE_OUTPUT_STREAM,
        schema_version=1,
        created_date=created,
        sequence_number=seq,
        flags=0,
        message_id=mid,
        payload=payload,
        payload_type=PayloadType.OUTPUT,
    )


@pytest.mark.asyncio
async def test_out_of_order_output_is_buffered_and_printed_in_order() -> None:
    cfg = WebSocketConfig(url="wss://example", token="T")
    dc = SessionDataChannel(cfg)

    printed: list[bytes] = []
    dc.set_input_handler(lambda b: printed.append(b))

    # Simulate receiving seq=1 first (future), then seq=0
    b1 = _build_output(1, "second")
    b0 = _build_output(0, "first\n")

    dc._handle_message(WebSocketMessage(message_type=MessageType.BINARY, data=b1))  # type: ignore[attr-defined]
    # Wait a bit for async processing
    await asyncio.sleep(0.01)
    assert printed == []  # not printed yet; buffered

    dc._handle_message(WebSocketMessage(message_type=MessageType.BINARY, data=b0))  # type: ignore[attr-defined]
    # Wait for async processing to complete
    await asyncio.sleep(0.01)

    # Should have printed seq 0 then drained seq 1
    combined = b"".join(printed)
    assert combined.startswith(b"first\n")
    assert b"second" in combined
