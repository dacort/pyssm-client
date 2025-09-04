"""Message parser for AWS SSM protocol messages."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from enum import Enum

from ..utils.logging import get_logger
from .protocol import parse_client_message, ClientMessage
from .types import WebSocketMessage, MessageType
from ..constants import (
    PayloadType,
    MESSAGE_OUTPUT_STREAM,
    MESSAGE_ACKNOWLEDGE,
    MESSAGE_CHANNEL_CLOSED,
    MESSAGE_START_PUBLICATION,
    MESSAGE_PAUSE_PUBLICATION,
)


class ParsedMessageType(Enum):
    """Categorized message types for easier handling."""

    HANDSHAKE_REQUEST = "handshake_request"
    HANDSHAKE_COMPLETE = "handshake_complete"
    ENCRYPTION_CHALLENGE = "encryption_challenge"
    SHELL_OUTPUT = "shell_output"
    CHANNEL_CLOSED = "channel_closed"
    START_PUBLICATION = "start_publication"
    PAUSE_PUBLICATION = "pause_publication"
    ACKNOWLEDGE = "acknowledge"
    TEXT_CONTROL = "text_control"
    UNKNOWN_BINARY = "unknown_binary"


class ParsedMessage:
    """Wrapper for parsed AWS SSM messages with categorization."""

    def __init__(
        self,
        message_type: ParsedMessageType,
        client_message: Optional[ClientMessage] = None,
        text_data: Optional[str] = None,
        raw_data: Optional[bytes] = None,
        parsed_payload: Optional[Dict[str, Any]] = None,
    ):
        self.message_type = message_type
        self.client_message = client_message
        self.text_data = text_data
        self.raw_data = raw_data
        self.parsed_payload = parsed_payload or {}


class MessageParser:
    """Parser for AWS SSM WebSocket messages."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    def parse_websocket_message(
        self, message: WebSocketMessage
    ) -> Optional[ParsedMessage]:
        """Parse a WebSocket message into a categorized ParsedMessage."""
        try:
            if message.message_type == MessageType.BINARY:
                return self._parse_binary_message(message)
            elif message.message_type == MessageType.TEXT:
                return self._parse_text_message(message)
            else:
                self.logger.debug(f"Unknown message type: {message.message_type}")
                return None
        except Exception as e:
            self.logger.error(f"Error parsing message: {e}")
            return None

    def _parse_binary_message(
        self, message: WebSocketMessage
    ) -> Optional[ParsedMessage]:
        """Parse binary AWS SSM protocol message."""
        if not isinstance(message.data, bytes):
            return None

        client_message = parse_client_message(message.data)
        if not client_message:
            self.logger.debug(
                f"Failed to parse binary message: {len(message.data)} bytes"
            )
            return ParsedMessage(
                message_type=ParsedMessageType.UNKNOWN_BINARY, raw_data=message.data
            )

        # Categorize based on payload type and message type
        # Always include raw_data for potential buffering
        if client_message.payload_type == PayloadType.HANDSHAKE_REQUEST:
            return ParsedMessage(
                message_type=ParsedMessageType.HANDSHAKE_REQUEST,
                client_message=client_message,
                raw_data=message.data,
                parsed_payload=self._parse_json_payload(client_message.payload),
            )
        elif client_message.payload_type == PayloadType.HANDSHAKE_COMPLETE:
            return ParsedMessage(
                message_type=ParsedMessageType.HANDSHAKE_COMPLETE,
                client_message=client_message,
                raw_data=message.data,
                parsed_payload=self._parse_json_payload(client_message.payload),
            )
        elif client_message.payload_type == PayloadType.ENC_CHALLENGE_REQUEST:
            return ParsedMessage(
                message_type=ParsedMessageType.ENCRYPTION_CHALLENGE,
                client_message=client_message,
                raw_data=message.data,
                parsed_payload=self._parse_json_payload(client_message.payload),
            )
        elif client_message.message_type.strip() == MESSAGE_CHANNEL_CLOSED:
            return ParsedMessage(
                message_type=ParsedMessageType.CHANNEL_CLOSED,
                client_message=client_message,
                raw_data=message.data,
                parsed_payload=self._parse_json_payload(client_message.payload),
            )
        elif client_message.message_type.strip() == MESSAGE_START_PUBLICATION:
            return ParsedMessage(
                message_type=ParsedMessageType.START_PUBLICATION,
                client_message=client_message,
                raw_data=message.data,
            )
        elif client_message.message_type.strip() == MESSAGE_PAUSE_PUBLICATION:
            return ParsedMessage(
                message_type=ParsedMessageType.PAUSE_PUBLICATION,
                client_message=client_message,
                raw_data=message.data,
            )
        elif client_message.is_shell_output():
            return ParsedMessage(
                message_type=ParsedMessageType.SHELL_OUTPUT,
                client_message=client_message,
                raw_data=message.data,
            )
        elif client_message.message_type.strip() == MESSAGE_ACKNOWLEDGE:
            return ParsedMessage(
                message_type=ParsedMessageType.ACKNOWLEDGE,
                client_message=client_message,
                raw_data=message.data,
            )
        else:
            self.logger.debug(
                f"Unknown binary message type: {client_message.message_type}"
            )
            return ParsedMessage(
                message_type=ParsedMessageType.UNKNOWN_BINARY,
                client_message=client_message,
                raw_data=message.data,
            )

    def _parse_text_message(self, message: WebSocketMessage) -> Optional[ParsedMessage]:
        """Parse text control message."""
        if not isinstance(message.data, str):
            return None

        self.logger.debug(f"Text message: {message.data[:200]}...")
        return ParsedMessage(
            message_type=ParsedMessageType.TEXT_CONTROL, text_data=message.data
        )

    def _parse_json_payload(self, payload: bytes) -> Dict[str, Any]:
        """Parse JSON payload, returning empty dict on failure."""
        try:
            result: Dict[str, Any] = json.loads(
                payload.decode("utf-8", errors="ignore")
            )
            return result
        except Exception as e:
            self.logger.debug(f"Failed to parse JSON payload: {e}")
            return {}
