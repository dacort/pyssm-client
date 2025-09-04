"""WebSocket communication utilities."""

from __future__ import annotations

import urllib.parse
from typing import Any

from .types import WebSocketConfig


def build_stream_url(base_url: str, session_id: str, token: str) -> str:
    """Build WebSocket stream URL with proper parameters."""
    if not base_url.startswith(("ws://", "wss://")):
        # Convert HTTP(S) to WebSocket
        if base_url.startswith("https://"):
            base_url = base_url.replace("https://", "wss://", 1)
        elif base_url.startswith("http://"):
            base_url = base_url.replace("http://", "ws://", 1)
        else:
            # Assume HTTPS for security
            base_url = f"wss://{base_url}"

    # Parse URL and add parameters
    parsed = urllib.parse.urlparse(base_url)

    # Build query parameters
    params = {"sessionId": session_id, "token": token}

    query_string = urllib.parse.urlencode(params)

    # Reconstruct URL
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            query_string,
            parsed.fragment,
        )
    )


def create_websocket_config(
    stream_url: str, token: str, **kwargs: Any
) -> WebSocketConfig:
    """Create WebSocket configuration with sensible defaults."""
    return WebSocketConfig(
        url=stream_url,
        token=token,
        ping_interval=kwargs.get("ping_interval", 30.0),
        ping_timeout=kwargs.get("ping_timeout", 10.0),
        connect_timeout=kwargs.get("connect_timeout", 30.0),
        max_message_size=kwargs.get("max_message_size", 1024 * 1024),
        max_queue_size=kwargs.get("max_queue_size", 100),
        retry_attempts=kwargs.get("retry_attempts", 3),
        retry_delay=kwargs.get("retry_delay", 1.0),
    )


def validate_websocket_url(url: str) -> bool:
    """Validate WebSocket URL format."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ("ws", "wss") and bool(parsed.netloc)
    except Exception:
        return False
