from __future__ import annotations

import json
import hashlib
import time
import uuid

from pyssm_client.communicator.protocol import (
    parse_client_message,
    serialize_client_message,
)
from pyssm_client.constants import (
    MESSAGE_OUTPUT_STREAM,
    MESSAGE_ACKNOWLEDGE,
    PayloadType,
)
from pyssm_client.communicator.protocol import create_acknowledge_message


def _build_output_message(seq: int, payload: bytes) -> bytes:
    mid = uuid.uuid4().bytes
    created = int(time.time() * 1000)
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


def test_ack_message_format_and_digest() -> None:
    # Build and parse a synthetic output message
    original_bytes = _build_output_message(7, b"hello")
    original = parse_client_message(original_bytes)
    assert original is not None

    # Create ack and parse it back
    ack_bytes = create_acknowledge_message(original)
    ack = parse_client_message(ack_bytes)
    assert ack is not None

    # Header fields
    assert ack.message_type.strip() == MESSAGE_ACKNOWLEDGE
    assert ack.schema_version == 1
    assert ack.sequence_number == 0
    assert ack.flags == 3
    assert ack.payload_type == 0  # acknowledge has payload_type 0

    # Payload should be JSON with acknowledged info
    payload_obj = json.loads(ack.payload.decode("utf-8"))
    assert (
        payload_obj["AcknowledgedMessageType"].strip() == original.message_type.strip()
    )
    assert (
        payload_obj["AcknowledgedMessageId"].strip()
        == original.get_message_id_string().strip()
    )
    assert payload_obj["AcknowledgedMessageSequenceNumber"] == original.sequence_number
    assert payload_obj.get("IsSequentialMessage") is True

    # Digest check: sha256(payload) must equal payload_digest
    digest = hashlib.sha256(ack.payload).digest()
    assert digest == ack.payload_digest
