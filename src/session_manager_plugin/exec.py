"""Programmatic single-command execution via Session Manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import boto3

from .cli.types import ConnectArguments
from .communicator.utils import create_websocket_config


def _filter_shell_output(data: bytes, original_command: str) -> bytes:
    """Filter shell prompts, command echoes, and ANSI sequences from output."""
    import re
    
    try:
        text = data.decode("utf-8", errors="ignore")
        
        # Remove ANSI escape sequences
        clean_text = re.sub(r'\x1b\[[?0-9;]*[a-zA-Z]', '', text)
        
        # Split into lines and filter
        lines = clean_text.split('\n')
        filtered_lines = []
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines, shell prompts, and command echoes
            if (line and
                not line.startswith('sh-') and  # Shell prompts
                not line.endswith('$') and     # Shell prompts  
                not line == original_command and  # Exact command echo
                not line.startswith('exit') and   # Exit command
                not line.startswith('printf ') and  # Printf sentinel
                not '__SSM_EXIT__' in line):      # Exit marker
                filtered_lines.append(line)
        
        result = '\n'.join(filtered_lines)
        if result.strip():
            return (result + '\n').encode("utf-8")
        else:
            return b''
            
    except Exception:
        # If filtering fails, return original data
        return data


@dataclass
class CommandResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    
    def __iter__(self):
        yield self.stdout
        yield self.stderr
        yield self.exit_code
    
    def __repr__(self) -> str:
        return f"CommandResult(exit_code={self.exit_code}, stdout={len(self.stdout)}B, stderr={len(self.stderr)}B)"


async def run_command(
    target: str,
    command: str,
    *,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    timeout: int = 600,
) -> CommandResult:
    """Execute a single command via SSM Standard_Stream and return stdout/stderr/exit_code.

    Streams are aggregated; callers can add their own streaming by wiring handlers here if desired.
    """
    # Boto3 session
    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    if region:
        session_kwargs["region_name"] = region
    session = boto3.Session(**session_kwargs)
    ssm = session.client("ssm", endpoint_url=endpoint_url)
    
    # Start SSM session
    ss = ssm.start_session(Target=target)
    args = ConnectArguments(
        session_id=ss["SessionId"],
        stream_url=ss["StreamUrl"],
        token_value=ss["TokenValue"],
        target=target,
        session_type="Standard_Stream",
    )
    
    # Registry and session  
    from .session.registry import get_session_registry
    from .session.plugins import StandardStreamPlugin
    from .session.session_handler import SessionHandler
    
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
    from .communicator.data_channel import SessionDataChannel
    
    ws_config = create_websocket_config(args.stream_url, args.token_value)
    data_channel = SessionDataChannel(ws_config)
    
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    loop = asyncio.get_running_loop()
    closed = asyncio.Event()
    
    def _on_stdout(data: bytes) -> None:
        # Keep all stdout for exit code parsing, filter later
        stdout_buf.extend(data)
    
    def _on_stderr(data: bytes) -> None:
        # Keep stderr as-is for exit code parsing (don't filter the marker!)
        stderr_buf.extend(data)
    
    def _on_closed() -> None:
        try:
            loop.call_soon_threadsafe(closed.set)
        except Exception:
            pass
    
    data_channel.set_stdout_handler(_on_stdout)
    data_channel.set_stderr_handler(_on_stderr)
    data_channel.set_closed_handler(_on_closed)
    
    # Execute session
    session_obj.set_data_channel(data_channel)
    await session_obj.execute()
    await asyncio.sleep(0.1)
    
    # Wrap command: emit exit code to stdout instead of stderr, then exit
    # Use a unique marker that won't interfere with command output
    wrapped = f"({command}); __EC=$?; echo '__SSM_EXIT__:'$__EC; exit $__EC"
    await data_channel.send_input_data((wrapped + "\n").encode("utf-8"))
    
    # Wait for session close or timeout
    try:
        await asyncio.wait_for(closed.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            if data_channel.is_open:
                await data_channel.close()
        except Exception:
            pass
        return CommandResult(bytes(stdout_buf), bytes(stderr_buf), 124)
    
    # Parse exit code from stdout (changed from stderr)
    exit_code = 1
    stdout_text = ""
    try:
        stdout_text = stdout_buf.decode("utf-8", errors="ignore")
        
        marker = "__SSM_EXIT__:" 
        if marker in stdout_text:
            last = stdout_text.rsplit(marker, 1)[-1]
            # extract up to newline/digits only
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
    
    # Filter exit code marker from stdout for return value
    if stdout_text and "__SSM_EXIT__:" in stdout_text:
        lines = stdout_text.split('\n')
        filtered_lines = [line for line in lines if '__SSM_EXIT__:' not in line]
        clean_stdout = '\n'.join(filtered_lines).rstrip('\n')
        if clean_stdout:
            final_stdout = _filter_shell_output(clean_stdout.encode("utf-8"), command)
        else:
            final_stdout = b''
    else:
        final_stdout = _filter_shell_output(bytes(stdout_buf), command)
    
    return CommandResult(final_stdout, bytes(stderr_buf), exit_code)
    
    
def run_command_sync(
    target: str,
    command: str,
    *,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    timeout: int = 600,
) -> CommandResult:
    """Synchronous wrapper for run_command."""
    return asyncio.run(
        run_command(
            target,
            command,
            profile=profile,
            region=region,
            endpoint_url=endpoint_url,
            timeout=timeout,
        )
    )