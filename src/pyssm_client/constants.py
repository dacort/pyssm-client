"""Shared constants and enums for the Session Manager plugin."""

from __future__ import annotations

from enum import IntEnum


# Public client version string used in handshakes and logging
CLIENT_VERSION: str = "pyssm-client/0.1.0"


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
