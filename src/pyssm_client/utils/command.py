"""Command execution utilities for Session Manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import boto3

# Avoid circular dependency - import ConnectArguments inside function
from ..communicator.utils import create_websocket_config


def _filter_shell_output(data: bytes, original_command: str) -> bytes:
    """Filter shell prompts, command echoes, and ANSI sequences from output."""
    import re

    try:
        text = data.decode("utf-8", errors="ignore")

        # Remove ANSI escape sequences
        clean_text = re.sub(r"\x1b\[[?0-9;]*[a-zA-Z]", "", text)

        # Split into lines and filter
        lines = clean_text.split("\n")
        filtered_lines = []

        for line in lines:
            line = line.strip()

            # Skip empty lines, shell prompts, and command echoes
            if (
                line
                and not line.startswith("sh-")  # Shell prompts
                and not line.endswith("$")  # Shell prompts
                and not line == original_command  # Exact command echo
                and not line.startswith("exit")  # Exit command
                and not line.startswith("printf ")  # Printf sentinel
                and "__SSM_EXIT__" not in line
            ):  # Exit marker
                filtered_lines.append(line)

        result = "\n".join(filtered_lines)
        if result.strip():
            return (result + "\n").encode("utf-8")
        else:
            return b""

    except Exception:
        # If filtering fails, return original data
        return data


@dataclass
class CommandResult:
    stdout: bytes
    stderr: bytes
    exit_code: int

    def __iter__(self) -> Iterator[Any]:
        yield self.stdout
        yield self.stderr
        yield self.exit_code

    def __repr__(self) -> str:
        return f"CommandResult(exit_code={self.exit_code}, stdout={len(self.stdout)}B, stderr={len(self.stderr)}B)"


async def run_command(
    target: str,
    command: str,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    timeout: int = 600,
) -> CommandResult:
    """Execute a single command on target and return stdout/stderr/exit_code.

    This is the async version. For synchronous usage, use run_command_sync().

    Args:
        target: EC2 instance or managed instance ID
        command: Shell command to execute
        profile: AWS profile name
        region: AWS region
        endpoint_url: Custom AWS endpoint URL
        timeout: Command timeout in seconds

    Returns:
        CommandResult with separated stdout, stderr, and exit code
    """
    # Create AWS session
    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    if region:
        session_kwargs["region_name"] = region

    session = boto3.Session(**session_kwargs)  # type: ignore[arg-type]
    ssm = session.client("ssm", endpoint_url=endpoint_url)

    # Start session
    ss = ssm.start_session(Target=target)

    # Import here to avoid circular dependency
    from ..cli.types import ConnectArguments

    # Build connection args
    args = ConnectArguments(
        session_id=ss["SessionId"],
        stream_url=ss["StreamUrl"],
        token_value=ss["TokenValue"],
        target=target,
        session_type="Standard_Stream",
    )

    # Registry and session
    from ..session.registry import get_session_registry
    from ..session.plugins import StandardStreamPlugin
    from ..session.session_handler import SessionHandler

    registry = get_session_registry()
    if not registry.is_session_type_supported("Standard_Stream"):
        registry.register_plugin("Standard_Stream", StandardStreamPlugin())
    handler = SessionHandler()

    session_obj = await handler.validate_input_and_create_session(
        {
            "sessionId": args.session_id,
            "streamUrl": args.stream_url,
            "tokenValue": args.token_value,
            "target": args.target,
            "sessionType": args.session_type,
        }
    )

    # Data channel and buffers
    from ..communicator.data_channel import SessionDataChannel

    ws_config = create_websocket_config(args.stream_url, args.token_value)
    data_channel = SessionDataChannel(ws_config)

    stdout_buf = bytearray()
    stderr_buf = bytearray()
    session_done = asyncio.Event()
    exit_code = 0

    def handle_stdout(data: bytes) -> None:
        """Handle stdout from shell."""
        nonlocal stdout_buf, exit_code

        # Check for exit status marker in stdout
        try:
            text = data.decode("utf-8", errors="ignore")
            if "__SSM_EXIT__:" in text:
                # Extract exit code
                marker = "__SSM_EXIT__:"
                last = text.rsplit(marker, 1)[-1]
                digits = []
                for ch in last:
                    if ch.isdigit():
                        digits.append(ch)
                    else:
                        break
                if digits:
                    exit_code = int("".join(digits))
        except Exception:
            pass

        stdout_buf.extend(data)

    def handle_stderr(data: bytes) -> None:
        """Handle stderr from shell."""
        nonlocal stderr_buf
        stderr_buf.extend(data)

    def handle_closed() -> None:
        session_done.set()

    # Configure data channel - use proper stream handlers for separated output
    data_channel.set_stdout_handler(handle_stdout)
    data_channel.set_stderr_handler(handle_stderr)
    data_channel.set_closed_handler(handle_closed)

    # Set client info and attach to session
    from ..constants import CLIENT_VERSION

    try:
        data_channel.set_client_info("pyssm-client", CLIENT_VERSION)
    except Exception:
        pass
    session_obj.set_data_channel(data_channel)

    session_id = ss["SessionId"]

    try:
        # Execute session
        await session_obj.execute()

        # Send command + exit status query with unique marker
        await asyncio.sleep(0.1)  # Brief pause for shell setup
        wrapped = f"({command}); __EC=$?; echo '__SSM_EXIT__:'$__EC; exit $__EC"
        await data_channel.send_input_data((wrapped + "\n").encode("utf-8"))

        # Wait for completion or timeout
        try:
            await asyncio.wait_for(session_done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            exit_code = 124  # Command timeout

        # Add delay to allow AWS to fully process command completion before termination
        await asyncio.sleep(0.2)

        # Filter shell noise from stdout and remove exit marker
        stdout_text = stdout_buf.decode("utf-8", errors="ignore")

        if "__SSM_EXIT__:" in stdout_text:
            lines = stdout_text.split("\n")
            filtered_lines = [line for line in lines if "__SSM_EXIT__:" not in line]
            clean_stdout = "\n".join(filtered_lines)
            final_stdout = _filter_shell_output(clean_stdout.encode("utf-8"), command)
        else:
            final_stdout = _filter_shell_output(bytes(stdout_buf), command)

        return CommandResult(
            stdout=final_stdout,
            stderr=bytes(stderr_buf),  # Now using proper stderr separation
            exit_code=exit_code,
        )

    finally:
        # Ensure both client-side and AWS-side sessions are terminated
        try:
            await session_obj.terminate_session()
        except Exception:
            pass

        try:
            ssm.terminate_session(SessionId=session_id)
        except Exception:
            pass


def run_command_sync(
    target: str,
    command: str,
    *,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    timeout: int = 600,
) -> CommandResult:
    """Synchronous wrapper for run_command()."""
    return asyncio.run(
        run_command(
            target=target,
            command=command,
            profile=profile,
            region=region,
            endpoint_url=endpoint_url,
            timeout=timeout,
        )
    )
