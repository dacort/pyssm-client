"""Main CLI interface for AWS Session Manager Plugin."""

import asyncio
import os
import shutil
import termios
import tty
import json
import logging
import signal
import sys
from typing import Any

import click

from .types import ConnectArguments, SSHArguments
from ..communicator.data_channel import SessionDataChannel
from ..constants import CLIENT_VERSION
from ..communicator.utils import create_websocket_config
from ..session.session_handler import SessionHandler
from ..session.types import ClientConfig, SessionConfig, SessionType
from ..utils.logging import get_logger, setup_logging


class SessionManagerPlugin:
    """Main plugin coordinator class."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._session_handler = SessionHandler()
        self._current_session: Any | None = None
        self._shutdown_event = asyncio.Event()
        self._orig_term_attrs: list[int] | None = None
        self._resize_task: asyncio.Task | None = None
        # Input coalescing configuration (managed via CLI)
        self._coalesce_mode: str = "auto"  # "auto" | "on" | "off"
        self._coalesce_delay_ms: float = 10.0

    async def run_session(self, args: ConnectArguments) -> int:
        """Run a session with the provided arguments."""
        try:
            # Validate arguments
            errors = args.validate()
            if errors:
                for error in errors:
                    self.logger.error(f"Validation error: {error}")
                return 1

            # Create and configure data channel
            data_channel = await self._create_data_channel(args)

            # Register session plugins
            await self._register_session_plugins()

            # Create session without auto-executing
            self._current_session = (
                await self._session_handler.validate_input_and_create_session(
                    {
                        "sessionId": args.session_id,
                        "streamUrl": args.stream_url,
                        "tokenValue": args.token_value,
                        "target": args.target,
                        "documentName": args.document_name,
                        "sessionType": args.session_type,
                        "clientId": args.client_id,
                        "parameters": args.get_parameters_dict(),
                    }
                )
            )

            # Set up data channel for session BEFORE executing
            # Also supply client metadata for handshake
            try:
                data_channel.set_client_info(self._current_session.client_id, CLIENT_VERSION)
            except Exception:
                pass
            self._current_session.set_data_channel(data_channel)
            
            # Now execute the session with data channel properly set
            await self._current_session.execute()

            # Set up signal handlers for graceful shutdown
            self._setup_signal_handlers()

            self.logger.debug(f"Session {args.session_id} started successfully")

            # If interactive TTY, configure terminal and send initial size
            if sys.stdin.isatty():
                self._enter_cbreak_noecho()
                await self._send_initial_terminal_size()
                self._start_resize_heartbeat()

            # Wait for session completion or shutdown signal
            await self._wait_for_completion()

            return 0

        except KeyboardInterrupt:
            self.logger.debug("Received interrupt signal")
            return 130  # SIGINT exit code
        except Exception as e:
            self.logger.error(f"Session failed: {e}", exc_info=True)
            return 1
        finally:
            await self._cleanup()

    def _create_session_config(self, args: ConnectArguments) -> SessionConfig:
        """Create session configuration from CLI arguments."""
        return SessionConfig(
            session_id=args.session_id,
            stream_url=args.stream_url,
            token_value=args.token_value,
            target=args.target,
            document_name=args.document_name,
            parameters=args.get_parameters_dict(),
        )

    def _create_client_config(self, args: ConnectArguments) -> ClientConfig:
        """Create client configuration from CLI arguments."""
        try:
            session_type = SessionType(args.session_type)
        except ValueError:
            raise ValueError(f"Unsupported session type: {args.session_type}")

        return ClientConfig(client_id=args.client_id or "", session_type=session_type)

    async def _create_data_channel(self, args: ConnectArguments) -> SessionDataChannel:
        """Create and configure data channel."""
        websocket_config = create_websocket_config(
            stream_url=args.stream_url, token=args.token_value
        )

        data_channel = SessionDataChannel(websocket_config)

        # Set up input/output handlers for different session types
        await self._configure_data_channel_handlers(data_channel, args)

        # Ensure we shutdown when the data channel closes
        def on_closed() -> None:
            try:
                asyncio.get_event_loop().create_task(self._initiate_shutdown())
            except RuntimeError:
                # If no running loop, fall back to setting the event synchronously
                if not self._shutdown_event.is_set():
                    self._shutdown_event.set()
        data_channel.set_closed_handler(on_closed)

        # Configure coalescing based on CLI setting
        try:
            mode = getattr(self, "_coalesce_mode", "auto").lower()
            delay_sec = max(0.0, float(getattr(self, "_coalesce_delay_ms", 10.0)) / 1000.0)
            if mode == "on":
                data_channel.set_coalescing(True, delay_sec=delay_sec)
                self.logger.debug(f"Input coalescing: enabled (delay={delay_sec}s)")
            elif mode == "off":
                data_channel.set_coalescing(False)
                self.logger.debug("Input coalescing: disabled")
            else:  # auto
                enabled = not sys.stdin.isatty()
                data_channel.set_coalescing(enabled, delay_sec=delay_sec)
                self.logger.debug(f"Input coalescing: auto -> {'enabled' if enabled else 'disabled'} (delay={delay_sec}s)")
        except Exception as e:
            self.logger.debug(f"Failed to configure coalescing: {e}")

        return data_channel

    async def _configure_data_channel_handlers(
        self, data_channel: SessionDataChannel, args: ConnectArguments
    ) -> None:
        """Configure data channel input/output handlers based on session type."""
        session_type = args.session_type

        if session_type == "Standard_Stream":
            # Set up stdin/stdout handlers
            data_channel.set_input_handler(self._handle_remote_input)
            data_channel.set_output_handler(self._handle_remote_output)
        elif session_type == "Port":
            # Port forwarding handlers would be different
            self.logger.info(
                "Port session type - specialized handlers not yet implemented"
            )
        else:
            self.logger.warning(f"Unknown session type: {session_type}")

    def _handle_remote_input(self, data: bytes) -> None:
        """Handle input data from remote session."""
        try:
            # Write to stdout
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except Exception as e:
            self.logger.error(f"Error writing remote input: {e}")

    def _handle_remote_output(self, data: bytes) -> None:
        """Handle output data to remote session (not typically used in standard flow)."""
        self.logger.debug(f"Remote output: {len(data)} bytes")

    async def _register_session_plugins(self) -> None:
        """Register session type plugins."""
        from ..session.plugins import StandardStreamPlugin

        registry = self._session_handler._registry
        registry.register_plugin("Standard_Stream", StandardStreamPlugin())

        self.logger.debug("Session plugins registered")

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        def sigint_handler(signum: int, frame: Any) -> None:
            # Forward Ctrl-C to remote instead of closing locally
            self.logger.debug("SIGINT: forwarding to remote as ETX")
            if (
                self._current_session
                and self._current_session.data_channel
                and self._current_session.data_channel.is_open
            ):
                loop.create_task(
                    self._current_session.data_channel.send_input_data(b"\x03")
                )
            else:
                loop.create_task(self._initiate_shutdown())

        def sigterm_handler(signum: int, frame: Any) -> None:
            self.logger.debug("SIGTERM: initiating shutdown")
            loop.create_task(self._initiate_shutdown())

        def sigwinch_handler(signum: int, frame: Any) -> None:
            # On terminal resize, send updated size
            loop.create_task(self._send_terminal_size_update())

        def sigquit_handler(signum: int, frame: Any) -> None:
            # Forward Ctrl-\ (FS) 0x1c
            self.logger.debug("SIGQUIT: forwarding to remote as FS (0x1c)")
            if (
                self._current_session
                and self._current_session.data_channel
                and self._current_session.data_channel.is_open
            ):
                loop.create_task(
                    self._current_session.data_channel.send_input_data(b"\x1c")
                )

        def sigtstp_handler(signum: int, frame: Any) -> None:
            # Forward Ctrl-Z (SUB) 0x1a
            self.logger.debug("SIGTSTP: forwarding to remote as SUB (0x1a)")
            if (
                self._current_session
                and self._current_session.data_channel
                and self._current_session.data_channel.is_open
            ):
                loop.create_task(
                    self._current_session.data_channel.send_input_data(b"\x1a")
                )

        # Prefer asyncio loop signal handlers on Unix for better integration
        try:
            if hasattr(signal, "SIGINT"):
                loop.add_signal_handler(signal.SIGINT, sigint_handler, signal.SIGINT, None)
            if hasattr(signal, "SIGTERM"):
                loop.add_signal_handler(signal.SIGTERM, sigterm_handler, signal.SIGTERM, None)
            if hasattr(signal, "SIGWINCH"):
                loop.add_signal_handler(signal.SIGWINCH, sigwinch_handler, signal.SIGWINCH, None)
            if hasattr(signal, "SIGQUIT"):
                loop.add_signal_handler(signal.SIGQUIT, sigquit_handler, signal.SIGQUIT, None)
            if hasattr(signal, "SIGTSTP"):
                loop.add_signal_handler(signal.SIGTSTP, sigtstp_handler, signal.SIGTSTP, None)
        except (NotImplementedError, RuntimeError):
            # Fallback to signal.signal when loop.add_signal_handler is unavailable (e.g., Windows)
            signal.signal(signal.SIGINT, sigint_handler)
            signal.signal(signal.SIGTERM, sigterm_handler)
            if hasattr(signal, "SIGWINCH"):
                signal.signal(signal.SIGWINCH, sigwinch_handler)
            if hasattr(signal, "SIGQUIT"):
                signal.signal(signal.SIGQUIT, sigquit_handler)
            if hasattr(signal, "SIGTSTP"):
                signal.signal(signal.SIGTSTP, sigtstp_handler)

    async def _send_initial_terminal_size(self) -> None:
        await self._send_terminal_size_update()

    async def _send_terminal_size_update(self) -> None:
        try:
            cols, rows = shutil.get_terminal_size(fallback=(80, 24))
            if (
                self._current_session
                and self._current_session.data_channel
                and self._current_session.data_channel.is_open
            ):
                await self._current_session.data_channel.send_terminal_size(cols, rows)
        except Exception as e:
            self.logger.debug(f"Failed to send terminal size: {e}")

    async def _initiate_shutdown(self) -> None:
        """Initiate graceful shutdown."""
        if not self._shutdown_event.is_set():
            self.logger.debug("Initiating shutdown...")
            self._shutdown_event.set()

    async def _wait_for_completion(self) -> None:
        """Wait for session completion or shutdown signal."""
        # Set up stdin reader for interactive sessions
        loop = asyncio.get_event_loop()
        stdin_task = None
        stdin_fd = None
        if sys.stdin.isatty():
            try:
                stdin_fd = sys.stdin.fileno()
                loop.add_reader(stdin_fd, self._on_stdin_ready)
                self.logger.debug("Registered stdin reader")
            except Exception as e:
                self.logger.debug(f"Failed to add_reader for stdin: {e}; falling back to thread reader")
                stdin_task = asyncio.create_task(self._handle_stdin_input())
        
        try:
            # Wait for shutdown signal
            await self._shutdown_event.wait()
        finally:
            if stdin_fd is not None:
                try:
                    loop.remove_reader(stdin_fd)
                except Exception:
                    pass
            # Stop resize heartbeat
            await self._stop_resize_heartbeat()
            if stdin_task:
                stdin_task.cancel()
                try:
                    await stdin_task
                except asyncio.CancelledError:
                    pass
            # Restore terminal if modified
            if sys.stdin.isatty():
                self._restore_terminal()

    async def _handle_stdin_input(self) -> None:
        """Handle stdin input for interactive sessions."""
        try:
            loop = asyncio.get_event_loop()

            while not self._shutdown_event.is_set():
                # Read from stdin in a non-blocking way
                data = await loop.run_in_executor(None, sys.stdin.buffer.read, 1024)

                if not data:
                    # EOF reached
                    break

                # Send data through current session's data channel
                if (
                    self._current_session
                    and self._current_session.data_channel
                    and self._current_session.data_channel.is_open
                ):
                    await self._current_session.data_channel.send_input_data(data)

        except Exception as e:
            self.logger.error(f"Error handling stdin: {e}")
        finally:
            await self._initiate_shutdown()

    def _on_stdin_ready(self) -> None:
        """Callback when stdin has data; reads and forwards to data channel."""
        try:
            if not (self._current_session and self._current_session.data_channel and self._current_session.data_channel.is_open):
                return
            fd = sys.stdin.fileno()
            # Read whatever is available up to 1024 bytes
            data = os.read(fd, 1024)
            if not data:
                # EOF
                asyncio.get_event_loop().create_task(self._initiate_shutdown())
                return
            asyncio.get_event_loop().create_task(
                self._current_session.data_channel.send_input_data(data)
            )
        except Exception as e:
            self.logger.error(f"stdin read error: {e}")

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._current_session:
            try:
                await self._current_session.terminate_session()
            except Exception as e:
                self.logger.error(f"Error terminating session: {e}")

        self.logger.debug("Cleanup completed")

    def _enter_cbreak_noecho(self) -> None:
        """Put terminal into cbreak mode and disable echo (like Go plugin)."""
        try:
            if not sys.stdin.isatty():
                return
            fd = sys.stdin.fileno()
            self._orig_term_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            # Disable echo
            attrs = termios.tcgetattr(fd)
            # lflag (index 3): turn off ECHO and ISIG so Ctrl-C/Z/\ are not handled locally
            attrs[3] = attrs[3] & ~termios.ECHO
            attrs[3] = attrs[3] & ~termios.ISIG
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
            self.logger.debug("Terminal set to cbreak -echo")
        except Exception as e:
            self.logger.debug(f"Failed to set terminal mode: {e}")

    def _restore_terminal(self) -> None:
        """Restore terminal settings if changed."""
        try:
            if self._orig_term_attrs and sys.stdin.isatty():
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._orig_term_attrs)
                self.logger.debug("Terminal settings restored")
        except Exception as e:
            self.logger.debug(f"Failed to restore terminal: {e}")

    def _start_resize_heartbeat(self) -> None:
        """Start periodic terminal size updates (every 500ms)."""
        if self._resize_task is not None and not self._resize_task.done():
            return
        async def _loop():
            try:
                while not self._shutdown_event.is_set():
                    try:
                        cols, rows = shutil.get_terminal_size(fallback=(80, 24))
                        if (
                            self._current_session
                            and self._current_session.data_channel
                            and self._current_session.data_channel.is_open
                        ):
                            await self._current_session.data_channel.send_terminal_size(cols, rows)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                pass
        self._resize_task = asyncio.create_task(_loop())

    async def _stop_resize_heartbeat(self) -> None:
        """Stop periodic terminal size updates."""
        if self._resize_task and not self._resize_task.done():
            self._resize_task.cancel()
            try:
                await self._resize_task
            except asyncio.CancelledError:
                pass
        self._resize_task = None


# Click CLI interface with subcommands
@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.option("--log-file", help="Log file path")
@click.option(
    "--coalesce-input",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Input coalescing mode: auto (default), on, or off",
)
@click.option(
    "--coalesce-delay-ms",
    type=float,
    default=10.0,
    show_default=True,
    help="Coalescing delay in milliseconds when enabled",
)
@click.pass_context
def cli(
    ctx: click.Context,
    verbose: bool,
    log_file: str | None,
    coalesce_input: str,
    coalesce_delay_ms: float,
) -> None:
    """AWS Session Manager Plugin - Python implementation."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["log_file"] = log_file
    ctx.obj["coalesce_input"] = coalesce_input.lower()
    ctx.obj["coalesce_delay_ms"] = coalesce_delay_ms

    # Set up logging
    log_level = logging.DEBUG if verbose else logging.INFO
    setup_logging(level=log_level, log_file=log_file)


@cli.command()
@click.argument("json_input", required=False)
@click.option("--session-id", help="Session ID")
@click.option("--stream-url", help="WebSocket stream URL")
@click.option("--token-value", help="Session token")
@click.option("--target", help="Target instance/resource")
@click.option("--document-name", help="SSM document name")
@click.option("--session-type", default="Standard_Stream", help="Session type")
@click.option("--client-id", help="Client identifier")
@click.option("--parameters", help="Session parameters (JSON)")
@click.option("--profile", help="AWS profile")
@click.option("--region", help="AWS region")
@click.option("--endpoint-url", help="AWS endpoint URL")
@click.pass_context
def connect(ctx: click.Context, json_input: str | None, **kwargs: Any) -> None:
    """
    Connect to existing session with direct parameters.

    This command is typically called by the AWS CLI with JSON input containing
    session parameters. It can also be called directly with individual options.
    """
    try:
        # Parse input - either JSON string or individual options
        if json_input:
            # Parse JSON input (typical AWS CLI usage)
            try:
                json_data = json.loads(json_input)
                args = ConnectArguments.from_dict(json_data)
            except json.JSONDecodeError as e:
                click.echo(f"Error parsing JSON input: {e}", err=True)
                sys.exit(1)
        else:
            # Use individual options
            # Filter out None values
            filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
            args = ConnectArguments.from_dict(filtered_kwargs)

        # Run the session
        plugin = SessionManagerPlugin()
        # Configure input coalescing
        plugin._coalesce_mode = ctx.obj.get("coalesce_input", "auto")  # type: ignore[attr-defined]
        plugin._coalesce_delay_ms = float(ctx.obj.get("coalesce_delay_ms", 10.0))  # type: ignore[attr-defined]
        exit_code = asyncio.run(plugin.run_session(args))
        sys.exit(exit_code)

    except Exception as e:
        click.echo(f"Fatal error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--target", required=True, help="Target EC2 instance or managed instance ID")
@click.option("--document-name", help="SSM document name")
@click.option("--parameters", help="Session parameters (JSON)")
@click.option("--profile", help="AWS profile")
@click.option("--region", help="AWS region")
@click.option("--endpoint-url", help="AWS endpoint URL")
@click.pass_context
def ssh(ctx: click.Context, **kwargs: Any) -> None:
    """
    Start an interactive SSH-like session with AWS SSM.
    
    This command uses AWS SSM APIs to create a new session and then
    connects to it automatically.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        # Parse arguments
        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        ssh_args = SSHArguments(**filtered_kwargs)

        # Validate arguments
        errors = ssh_args.validate()
        if errors:
            for error in errors:
                click.echo(f"Validation error: {error}", err=True)
            sys.exit(1)

        # Set up AWS session
        session_kwargs = {}
        if ssh_args.profile:
            session_kwargs["profile_name"] = ssh_args.profile
        if ssh_args.region:
            session_kwargs["region_name"] = ssh_args.region

        session = boto3.Session(**session_kwargs)
        ssm = session.client("ssm", endpoint_url=ssh_args.endpoint_url)

        # Build start_session parameters
        params = {"Target": ssh_args.target}
        if ssh_args.document_name:
            params["DocumentName"] = ssh_args.document_name
        if ssh_args.parameters:
            params["Parameters"] = ssh_args.parameters

        # Start session via SSM API
        try:
            click.echo(f"Starting SSM session to {ssh_args.target}...")
            response = ssm.start_session(**params)
        except (BotoCoreError, ClientError) as e:
            click.echo(f"Failed to start SSM session: {e}", err=True)
            sys.exit(1)

        # Extract session details
        session_id = response["SessionId"]
        token_value = response["TokenValue"]
        stream_url = response["StreamUrl"]

        click.echo(f"Session started: {session_id}")

        # Convert to ConnectArguments and run session
        connect_args = ConnectArguments(
            session_id=session_id,
            stream_url=stream_url,
            token_value=token_value,
            target=ssh_args.target,
            document_name=ssh_args.document_name,
            session_type=ssh_args.session_type,
        )

        # Run the session
        plugin = SessionManagerPlugin()
        # Configure input coalescing
        plugin._coalesce_mode = ctx.obj.get("coalesce_input", "auto")  # type: ignore[attr-defined]
        plugin._coalesce_delay_ms = float(ctx.obj.get("coalesce_delay_ms", 10.0))  # type: ignore[attr-defined]
        exit_code = asyncio.run(plugin.run_session(connect_args))
        sys.exit(exit_code)

    except Exception as e:
        click.echo(f"Fatal error: {e}", err=True)
        sys.exit(1)


def main() -> int:
    """Main entry point."""
    cli()
    return 0


if __name__ == "__main__":
    main()
