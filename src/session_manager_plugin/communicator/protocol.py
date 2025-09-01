"""AWS SSM binary protocol parser for ClientMessage format."""

from __future__ import annotations

import json
import struct
import hashlib
import time
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Optional
from uuid import UUID


# Protocol constants from Go implementation
class PayloadType(IntEnum):
    OUTPUT = 1
    ERROR = 2
    SIZE = 3
    PARAMETER = 4
    HANDSHAKE_REQUEST = 5
    HANDSHAKE_RESPONSE = 6
    HANDSHAKE_COMPLETE = 7
    ENC_CHALLENGE_REQUEST = 8
    ENC_CHALLENGE_RESPONSE = 9
    FLAG = 10
    STDERR = 11
    EXIT_CODE = 12

# Message name constants (string fields in ClientMessage)
MESSAGE_INPUT_STREAM = "input_stream_data"
MESSAGE_OUTPUT_STREAM = "output_stream_data"
MESSAGE_ACKNOWLEDGE = "acknowledge"
MESSAGE_CHANNEL_CLOSED = "channel_closed"
MESSAGE_START_PUBLICATION = "start_publication"
MESSAGE_PAUSE_PUBLICATION = "pause_publication"


# ClientMessage field offsets and lengths (from Go source)
class ClientMessageOffsets:
    HL_LENGTH = 4
    MESSAGE_TYPE_LENGTH = 32
    SCHEMA_VERSION_LENGTH = 4
    CREATED_DATE_LENGTH = 8
    SEQUENCE_NUMBER_LENGTH = 8
    FLAGS_LENGTH = 8
    MESSAGE_ID_LENGTH = 16
    PAYLOAD_DIGEST_LENGTH = 32
    PAYLOAD_TYPE_LENGTH = 4
    PAYLOAD_LENGTH_LENGTH = 4

    HL_OFFSET = 0
    MESSAGE_TYPE_OFFSET = HL_OFFSET + HL_LENGTH
    SCHEMA_VERSION_OFFSET = MESSAGE_TYPE_OFFSET + MESSAGE_TYPE_LENGTH
    CREATED_DATE_OFFSET = SCHEMA_VERSION_OFFSET + SCHEMA_VERSION_LENGTH
    SEQUENCE_NUMBER_OFFSET = CREATED_DATE_OFFSET + CREATED_DATE_LENGTH
    FLAGS_OFFSET = SEQUENCE_NUMBER_OFFSET + SEQUENCE_NUMBER_LENGTH
    MESSAGE_ID_OFFSET = FLAGS_OFFSET + FLAGS_LENGTH
    PAYLOAD_DIGEST_OFFSET = MESSAGE_ID_OFFSET + MESSAGE_ID_LENGTH
    PAYLOAD_TYPE_OFFSET = PAYLOAD_DIGEST_OFFSET + PAYLOAD_DIGEST_LENGTH
    PAYLOAD_LENGTH_OFFSET = PAYLOAD_TYPE_OFFSET + PAYLOAD_TYPE_LENGTH
    PAYLOAD_OFFSET = PAYLOAD_LENGTH_OFFSET + PAYLOAD_LENGTH_LENGTH


@dataclass
class ClientMessage:
    """Parsed AWS SSM ClientMessage structure."""
    header_length: int
    message_type: str
    schema_version: int
    created_date: int
    sequence_number: int
    flags: int
    message_id: bytes
    payload_digest: bytes
    payload_type: int
    payload_length: int
    payload: bytes

    def is_output_stream(self) -> bool:
        """Check if this is an output stream message."""
        return self.message_type.strip() == MESSAGE_OUTPUT_STREAM

    def is_shell_output(self) -> bool:
        """Check if this contains shell output data."""
        return self.payload_type in (PayloadType.OUTPUT, PayloadType.STDERR)

    def get_shell_data(self) -> str:
        """Get shell data as string."""
        if self.is_shell_output():
            return self.payload.decode('utf-8', errors='replace')
        return ""

    def get_message_id_string(self) -> str:
        """Get message ID as UUID string."""
        try:
            # Go writes UUID on-wire as: least-significant 8 bytes, then most-significant 8 bytes.
            # Reconstruct RFC-4122 byte order as most + least before parsing.
            if len(self.message_id) >= 16:
                most = self.message_id[8:16]
                least = self.message_id[0:8]
                reordered = most + least
                message_uuid = UUID(bytes=reordered)
                return str(message_uuid)
            # Fallback if unexpected length
            message_uuid = UUID(bytes=self.message_id)
            return str(message_uuid)
        except (ValueError, TypeError):
            # Fallback to hex string if UUID parsing fails
            return self.message_id.hex().upper()


def parse_client_message(data: bytes) -> Optional[ClientMessage]:
    """
    Parse AWS SSM binary ClientMessage format.
    
    Args:
        data: Binary message data from WebSocket
        
    Returns:
        Parsed ClientMessage or None if parsing fails
    """
    try:
        if len(data) < ClientMessageOffsets.PAYLOAD_OFFSET:
            return None

        # Parse header length first
        header_length = struct.unpack('>I', data[
            ClientMessageOffsets.HL_OFFSET:
            ClientMessageOffsets.HL_OFFSET + ClientMessageOffsets.HL_LENGTH
        ])[0]

        # Parse message type (32 bytes, null-padded string)
        message_type_bytes = data[
            ClientMessageOffsets.MESSAGE_TYPE_OFFSET:
            ClientMessageOffsets.MESSAGE_TYPE_OFFSET + ClientMessageOffsets.MESSAGE_TYPE_LENGTH
        ]
        message_type = message_type_bytes.rstrip(b'\x00 ').decode('utf-8', errors='ignore')

        # Parse schema version
        schema_version = struct.unpack('>I', data[
            ClientMessageOffsets.SCHEMA_VERSION_OFFSET:
            ClientMessageOffsets.SCHEMA_VERSION_OFFSET + ClientMessageOffsets.SCHEMA_VERSION_LENGTH
        ])[0]

        # Parse created date
        created_date = struct.unpack('>Q', data[
            ClientMessageOffsets.CREATED_DATE_OFFSET:
            ClientMessageOffsets.CREATED_DATE_OFFSET + ClientMessageOffsets.CREATED_DATE_LENGTH
        ])[0]

        # Parse sequence number
        sequence_number = struct.unpack('>q', data[
            ClientMessageOffsets.SEQUENCE_NUMBER_OFFSET:
            ClientMessageOffsets.SEQUENCE_NUMBER_OFFSET + ClientMessageOffsets.SEQUENCE_NUMBER_LENGTH
        ])[0]

        # Parse flags
        flags = struct.unpack('>Q', data[
            ClientMessageOffsets.FLAGS_OFFSET:
            ClientMessageOffsets.FLAGS_OFFSET + ClientMessageOffsets.FLAGS_LENGTH
        ])[0]

        # Parse message ID (16 bytes UUID)
        message_id = data[
            ClientMessageOffsets.MESSAGE_ID_OFFSET:
            ClientMessageOffsets.MESSAGE_ID_OFFSET + ClientMessageOffsets.MESSAGE_ID_LENGTH
        ]

        # Parse payload digest (32 bytes SHA-256)
        payload_digest = data[
            ClientMessageOffsets.PAYLOAD_DIGEST_OFFSET:
            ClientMessageOffsets.PAYLOAD_DIGEST_OFFSET + ClientMessageOffsets.PAYLOAD_DIGEST_LENGTH
        ]

        # Parse payload type
        payload_type = struct.unpack('>I', data[
            ClientMessageOffsets.PAYLOAD_TYPE_OFFSET:
            ClientMessageOffsets.PAYLOAD_TYPE_OFFSET + ClientMessageOffsets.PAYLOAD_TYPE_LENGTH
        ])[0]

        # Parse payload length
        payload_length = struct.unpack('>I', data[
            ClientMessageOffsets.PAYLOAD_LENGTH_OFFSET:
            ClientMessageOffsets.PAYLOAD_LENGTH_OFFSET + ClientMessageOffsets.PAYLOAD_LENGTH_LENGTH
        ])[0]

        # Extract payload based on header length
        payload_start = header_length + ClientMessageOffsets.PAYLOAD_LENGTH_LENGTH
        payload = data[payload_start:payload_start + payload_length]

        return ClientMessage(
            header_length=header_length,
            message_type=message_type,
            schema_version=schema_version,
            created_date=created_date,
            sequence_number=sequence_number,
            flags=flags,
            message_id=message_id,
            payload_digest=payload_digest,
            payload_type=payload_type,
            payload_length=payload_length,
            payload=payload
        )

    except (struct.error, IndexError, UnicodeDecodeError) as e:
        # Return None for parsing failures - let caller handle
        return None


@dataclass
class AcknowledgeContent:
    """Acknowledgment content for AWS SSM protocol."""
    acknowledged_message_type: str
    acknowledged_message_id: str
    acknowledged_message_sequence_number: int
    is_sequential_message: bool = True

    def to_dict(self) -> Dict[str, any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "AcknowledgedMessageType": self.acknowledged_message_type,
            "AcknowledgedMessageId": self.acknowledged_message_id, 
            "AcknowledgedMessageSequenceNumber": self.acknowledged_message_sequence_number,
            "IsSequentialMessage": self.is_sequential_message
        }


def create_acknowledge_message(original_message: ClientMessage) -> bytes:
    """
    Create acknowledgment message for a received ClientMessage.
    
    Args:
        original_message: The message to acknowledge
        
    Returns:
        Binary acknowledgment message to send back to server
    """
    # Create acknowledge content
    ack_content = AcknowledgeContent(
        acknowledged_message_type=original_message.message_type.strip(),
        acknowledged_message_id=original_message.get_message_id_string(),
        acknowledged_message_sequence_number=original_message.sequence_number
    )
    
    # Serialize acknowledge content as JSON
    ack_payload = json.dumps(ack_content.to_dict()).encode('utf-8')
    
    # Create acknowledge message
    message_id = uuid.uuid4()
    created_date = int(time.time() * 1000)  # Unix epoch millis
    
    return serialize_client_message(
        message_type=MESSAGE_ACKNOWLEDGE,
        schema_version=1,
        created_date=created_date,
        sequence_number=0,  # Acknowledge messages have sequence 0
        flags=3,  # SYN + FIN flags as per Go implementation
        message_id=message_id.bytes,
        payload=ack_payload
    )


def _write_uuid_like_go(buf: bytearray, offset: int, message_id: bytes) -> None:
    """Write UUID bytes using the same layout as Go putUuid.

    Go writes two int64 values in big-endian: least significant 8 bytes first,
    then most significant 8 bytes. Mirror that here for compatibility.
    """
    if len(message_id) < 16:
        raise ValueError("message_id must be 16 bytes")

    ls = int.from_bytes(message_id[8:16], byteorder='big', signed=True)
    ms = int.from_bytes(message_id[0:8], byteorder='big', signed=True)

    struct.pack_into('>q', buf, ClientMessageOffsets.MESSAGE_ID_OFFSET, ls)
    struct.pack_into('>q', buf, ClientMessageOffsets.MESSAGE_ID_OFFSET + 8, ms)


def _write_message_type(buf: bytearray, message_type: str) -> None:
    """Write MessageType padded with spaces to 32 bytes (matches Go)."""
    field_len = ClientMessageOffsets.MESSAGE_TYPE_LENGTH
    start = ClientMessageOffsets.MESSAGE_TYPE_OFFSET
    end = start + field_len
    # Fill with spaces, then copy
    buf[start:end] = b' ' * field_len
    mt = message_type.encode('utf-8')[:field_len]
    buf[start:start + len(mt)] = mt


def serialize_client_message(
    message_type: str,
    schema_version: int,
    created_date: int,
    sequence_number: int,
    flags: int,
    message_id: bytes,
    payload: bytes,
    payload_type: int = 0,
) -> bytes:
    """
    Serialize ClientMessage to AWS SSM binary format.
    
    Based on Go implementation structure:
    HL | MessageType | Ver | CD | Seq | Flags | MessageId | Digest | PayType | PayLen | Payload
    """
    # Calculate lengths
    payload_length = len(payload)
    header_length = ClientMessageOffsets.PAYLOAD_LENGTH_OFFSET  # Up to PayloadLength field = 116
    total_length = header_length + ClientMessageOffsets.PAYLOAD_LENGTH_LENGTH + payload_length
    
    # Create buffer
    result = bytearray(total_length)
    
    # Header length (4 bytes) - this is the key fix!
    struct.pack_into('>I', result, ClientMessageOffsets.HL_OFFSET, header_length)
    
    # Message type (32 bytes, space-padded like Go)
    _write_message_type(result, message_type)
    
    # Schema version (4 bytes)
    struct.pack_into('>I', result, ClientMessageOffsets.SCHEMA_VERSION_OFFSET, schema_version)
    
    # Created date (8 bytes)
    struct.pack_into('>Q', result, ClientMessageOffsets.CREATED_DATE_OFFSET, created_date)
    
    # Sequence number (8 bytes, signed)
    struct.pack_into('>q', result, ClientMessageOffsets.SEQUENCE_NUMBER_OFFSET, sequence_number)
    
    # Flags (8 bytes)
    struct.pack_into('>Q', result, ClientMessageOffsets.FLAGS_OFFSET, flags)
    
    # Message ID (16 bytes) in Go layout
    _write_uuid_like_go(result, ClientMessageOffsets.MESSAGE_ID_OFFSET, message_id[:16])
    
    # Payload digest (32 bytes) - compute SHA-256 of payload
    digest = hashlib.sha256(payload).digest() if payload else bytes(32)
    start = ClientMessageOffsets.PAYLOAD_DIGEST_OFFSET
    end = start + ClientMessageOffsets.PAYLOAD_DIGEST_LENGTH
    result[start:end] = digest
    
    # Payload type (4 bytes)
    struct.pack_into('>I', result, ClientMessageOffsets.PAYLOAD_TYPE_OFFSET, payload_type)
    
    # Payload length (4 bytes) 
    struct.pack_into('>I', result, ClientMessageOffsets.PAYLOAD_LENGTH_OFFSET, payload_length)
    
    # Payload - place it right after PayloadLength field
    if payload:
        payload_start = ClientMessageOffsets.PAYLOAD_LENGTH_OFFSET + ClientMessageOffsets.PAYLOAD_LENGTH_LENGTH
        result[payload_start:payload_start + len(payload)] = payload
    
    return bytes(result)


## Note: Legacy helper serialize_client_message_with_payload_type was merged into
## serialize_client_message via the payload_type parameter to keep one implementation.
