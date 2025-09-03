"""Specialized message handlers for AWS SSM protocol."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Dict, Optional

from ..utils.logging import get_logger
from .message_parser import ParsedMessage, ParsedMessageType
from .protocol import ClientMessage
from ..constants import (
    PayloadType,
    CLIENT_VERSION,
    MESSAGE_INPUT_STREAM,
)


class MessageHandlerContext:
    """Context object passed to message handlers."""
    
    def __init__(
        self,
        send_message: Callable,  # Can be sync or async
        trigger_closed: Callable[[], None],
        input_handler: Optional[Callable[[bytes], None]] = None,
        stdout_handler: Optional[Callable[[bytes], None]] = None,
        stderr_handler: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        self.send_message = send_message
        self.trigger_closed = trigger_closed
        self.input_handler = input_handler
        self.stdout_handler = stdout_handler
        self.stderr_handler = stderr_handler


class HandshakeHandler:
    """Handler for handshake-related messages."""
    
    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._agent_version: Optional[str] = None
        self._session_type: Optional[str] = None
        self._session_properties: Optional[Dict[str, Any]] = None
    
    async def handle_handshake_request(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext,
        serialize_with_payload_type: Callable[[bytes, int], bytes]
    ) -> bool:
        """Handle handshake request message."""
        try:
            response = {
                "ClientVersion": CLIENT_VERSION,
                "ProcessedClientActions": [],
                "Errors": [],
            }
            
            request = message.parsed_payload
            actions = request.get("RequestedClientActions", [])
            self._agent_version = request.get("AgentVersion") or self._agent_version
            
            for action in actions:
                atype = action.get("ActionType")
                processed = {"ActionType": atype, "ActionStatus": 1}
                
                if atype == "SessionType":
                    ap = action.get("ActionParameters") or {}
                    if isinstance(ap, str):
                        try:
                            ap = json.loads(ap)
                        except Exception:
                            ap = {}
                    self._session_type = ap.get("SessionType") or self._session_type or ""
                    self._session_properties = ap.get("Properties") or {}
                    processed["ActionResult"] = action.get("ActionParameters")
                elif atype == "KMSEncryption":
                    processed["ActionStatus"] = 3
                    processed["Error"] = "KMSEncryption not supported in Python client"
                else:
                    processed["ActionStatus"] = 3
                    processed["Error"] = f"Unsupported action {atype}"
                
                processed_actions = response["ProcessedClientActions"]
                if isinstance(processed_actions, list):
                    processed_actions.append(processed)
            
            payload = json.dumps(response).encode("utf-8")
            msg = serialize_with_payload_type(payload, PayloadType.HANDSHAKE_RESPONSE)
            await context.send_message(msg)
            
            self.logger.debug("Sent HandshakeResponse")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to handle handshake request: {e}")
            return False
    
    def handle_handshake_complete(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext
    ) -> bool:
        """Handle handshake complete message."""
        try:
            payload = message.parsed_payload
            cust_msg = payload.get("CustomerMessage") or payload.get("customerMessage")
            
            if cust_msg and context.input_handler:
                context.input_handler((cust_msg + "\n").encode("utf-8"))
            
            summary = {
                "agent_version": self._agent_version or "",
                "session_type": self._session_type or "",
                "client_version": CLIENT_VERSION,
            }
            
            self.logger.debug(
                "Handshake complete: agent_version=%s, session_type=%s, client_version=%s",
                summary["agent_version"],
                summary["session_type"],
                summary["client_version"],
            )
            return True
            
        except Exception as e:
            self.logger.debug(f"Failed to parse HandshakeComplete payload: {e}")
            return False
    
    def handle_encryption_challenge(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext
    ) -> bool:
        """Handle encryption challenge message."""
        self.logger.info("Encryption challenge not supported; ignoring.")
        return True


class StreamHandler:
    """Handler for stream data messages."""
    
    def __init__(self) -> None:
        self.logger = get_logger(__name__)
    
    def handle_shell_output(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext,
        expected_sequence: int
    ) -> tuple[bool, Optional[int]]:
        """Handle shell output message.
        
        Returns:
            (processed, new_expected_sequence or None)
        """
        if not message.client_message:
            return False, None
            
        client_message = message.client_message
        seq = client_message.sequence_number
        
        if seq == expected_sequence:
            shell_data = client_message.get_shell_data()
            if shell_data and context.input_handler:
                context.input_handler(shell_data.encode("utf-8"))
            
            # Route to per-stream handlers
            try:
                if client_message.payload_type == PayloadType.OUTPUT:
                    if context.stdout_handler:
                        context.stdout_handler(shell_data.encode("utf-8"))
                elif client_message.payload_type == PayloadType.STDERR:
                    if context.stderr_handler:
                        context.stderr_handler(shell_data.encode("utf-8"))
            except Exception:
                pass
            
            return True, expected_sequence + 1
        elif seq > expected_sequence:
            # Buffer for later processing
            return True, None
        else:
            # Old message; ignore
            return False, None


class ControlHandler:
    """Handler for control messages."""
    
    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._closed_message_printed = False
    
    def handle_channel_closed(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext
    ) -> bool:
        """Handle channel closed message."""
        try:
            payload = message.parsed_payload
            output = (
                payload.get("Output") or 
                payload.get("output") or 
                "Session closed."
            )
            sess_id = payload.get("SessionId") or payload.get("sessionId")
            
            if not self._closed_message_printed and context.input_handler:
                if sess_id:
                    msg = f"\n\nSessionId: {sess_id} : {output}\n\n"
                else:
                    msg = f"\n\n{output}\n\n"
                context.input_handler(msg.encode("utf-8"))
                self._closed_message_printed = True
            
            context.trigger_closed()
            return True
            
        except Exception:
            output = "Session closed."
            if not self._closed_message_printed and context.input_handler:
                context.input_handler(f"\n\n{output}\n\n".encode("utf-8"))
                self._closed_message_printed = True
            context.trigger_closed()
            return True
    
    def handle_start_publication(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext
    ) -> bool:
        """Handle start publication message."""
        self.logger.debug("Received start_publication; input allowed")
        return True
    
    def handle_pause_publication(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext
    ) -> bool:
        """Handle pause publication message."""
        self.logger.debug("Received pause_publication; input paused")
        return True


class MessageRouter:
    """Routes parsed messages to appropriate handlers."""
    
    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.handshake_handler = HandshakeHandler()
        self.stream_handler = StreamHandler()
        self.control_handler = ControlHandler()
    
    async def route_message(
        self,
        message: ParsedMessage,
        context: MessageHandlerContext,
        expected_sequence: int,
        serialize_with_payload_type: Callable[[bytes, int], bytes]
    ) -> tuple[bool, Optional[int], bool]:
        """Route message to appropriate handler.
        
        Returns:
            (processed, new_expected_sequence, input_allowed_change)
        """
        try:
            if message.message_type == ParsedMessageType.HANDSHAKE_REQUEST:
                processed = await self.handshake_handler.handle_handshake_request(
                    message, context, serialize_with_payload_type
                )
                return processed, None, False
                
            elif message.message_type == ParsedMessageType.HANDSHAKE_COMPLETE:
                processed = self.handshake_handler.handle_handshake_complete(message, context)
                return processed, None, False
                
            elif message.message_type == ParsedMessageType.ENCRYPTION_CHALLENGE:
                processed = self.handshake_handler.handle_encryption_challenge(message, context)
                return processed, None, False
                
            elif message.message_type == ParsedMessageType.SHELL_OUTPUT:
                processed, new_seq = self.stream_handler.handle_shell_output(
                    message, context, expected_sequence
                )
                return processed, new_seq, False
                
            elif message.message_type == ParsedMessageType.CHANNEL_CLOSED:
                processed = self.control_handler.handle_channel_closed(message, context)
                return processed, None, False
                
            elif message.message_type == ParsedMessageType.START_PUBLICATION:
                processed = self.control_handler.handle_start_publication(message, context)
                return processed, None, True  # Input allowed
                
            elif message.message_type == ParsedMessageType.PAUSE_PUBLICATION:
                processed = self.control_handler.handle_pause_publication(message, context)
                return processed, None, False  # Input paused
                
            elif message.message_type == ParsedMessageType.TEXT_CONTROL:
                # Text frames are control/handshake; already logged in parser
                return True, None, False
                
            elif message.message_type == ParsedMessageType.ACKNOWLEDGE:
                # Acknowledgments are informational
                return True, None, False
                
            else:
                self.logger.debug(f"Unhandled message type: {message.message_type}")
                # Fallback for unknown messages
                if message.raw_data and context.input_handler:
                    context.input_handler(message.raw_data)
                return True, None, False
                
        except Exception as e:
            self.logger.error(f"Error routing message: {e}")
            return False, None, False