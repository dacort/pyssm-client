"""Session coordination and management for CLI operations."""

import asyncio
import os
import shutil
import signal
import sys
import termios
import tty
from typing import Any, Callable, Optional

from ..communicator.data_channel import SessionDataChannel
from ..communicator.utils import create_websocket_config
from ..constants import CLIENT_VERSION
from ..file_transfer.client import FileTransferClient
from ..file_transfer.types import (
    ChecksumType,
    FileTransferOptions,
)
from ..session.plugins import StandardStreamPlugin
from ..session.session_handler import SessionHandler
from ..session.types import ClientConfig, SessionConfig, SessionType
from ..utils.logging import get_logger
from .types import ConnectArguments


class SessionManagerPlugin:
    """Main plugin coordinator class for managing SSM sessions."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._session_handler = SessionHandler()
        self._current_session: Any | None = None
        self._shutdown_event = asyncio.Event()
        self._orig_term_attrs: Any = None
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
                data_channel.set_client_info("pyssm-client", CLIENT_VERSION)
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

            # Send any initial input if provided, e.g. `exec bash`
            if getattr(args, "initial_input", None):
                await data_channel.send_input_data(
                    f"{args.initial_input}\n".encode("utf-8")
                )

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
            delay_sec = max(
                0.0, float(getattr(self, "_coalesce_delay_ms", 10.0)) / 1000.0
            )
            if mode == "on":
                data_channel.set_coalescing(True, delay_sec=delay_sec)
                self.logger.debug(f"Input coalescing: enabled (delay={delay_sec}s)")
            elif mode == "off":
                data_channel.set_coalescing(False)
                self.logger.debug("Input coalescing: disabled")
            else:  # auto
                enabled = not sys.stdin.isatty()
                data_channel.set_coalescing(enabled, delay_sec=delay_sec)
                self.logger.debug(
                    f"Input coalescing: auto -> {'enabled' if enabled else 'disabled'} (delay={delay_sec}s)"
                )
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
        registry = self._session_handler._registry
        registry.register_plugin("Standard_Stream", StandardStreamPlugin())

        self.logger.debug("Session plugins registered")

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        def sigint_handler(_signum: int, _frame: Any) -> None:
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

        def sigterm_handler(_signum: int, _frame: Any) -> None:
            self.logger.debug("SIGTERM: initiating shutdown")
            loop.create_task(self._initiate_shutdown())

        def sigwinch_handler(_signum: int, _frame: Any) -> None:
            # On terminal resize, send updated size
            loop.create_task(self._send_terminal_size_update())

        def sigquit_handler(_signum: int, _frame: Any) -> None:
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

        def sigtstp_handler(_signum: int, _frame: Any) -> None:
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
                loop.add_signal_handler(
                    signal.SIGINT, sigint_handler, signal.SIGINT, None
                )
            if hasattr(signal, "SIGTERM"):
                loop.add_signal_handler(
                    signal.SIGTERM, sigterm_handler, signal.SIGTERM, None
                )
            if hasattr(signal, "SIGWINCH"):
                loop.add_signal_handler(
                    signal.SIGWINCH, sigwinch_handler, signal.SIGWINCH, None
                )
            if hasattr(signal, "SIGQUIT"):
                loop.add_signal_handler(
                    signal.SIGQUIT, sigquit_handler, signal.SIGQUIT, None
                )
            if hasattr(signal, "SIGTSTP"):
                loop.add_signal_handler(
                    signal.SIGTSTP, sigtstp_handler, signal.SIGTSTP, None
                )
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
                self.logger.debug(
                    f"Failed to add_reader for stdin: {e}; falling back to thread reader"
                )
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
            if not (
                self._current_session
                and self._current_session.data_channel
                and self._current_session.data_channel.is_open
            ):
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
                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self._orig_term_attrs
                )
                self.logger.debug("Terminal settings restored")
        except Exception as e:
            self.logger.debug(f"Failed to restore terminal: {e}")

    def _start_resize_heartbeat(self) -> None:
        """Start periodic terminal size updates (every 500ms)."""
        if self._resize_task is not None and not self._resize_task.done():
            return

        async def _loop() -> None:
            try:
                while not self._shutdown_event.is_set():
                    try:
                        cols, rows = shutil.get_terminal_size(fallback=(80, 24))
                        if (
                            self._current_session
                            and self._current_session.data_channel
                            and self._current_session.data_channel.is_open
                        ):
                            await self._current_session.data_channel.send_terminal_size(
                                cols, rows
                            )
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

    async def upload_file(
        self,
        local_path: str,
        remote_path: str,
        target: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        verify_checksum: bool = True,
        chunk_size: int = 32768,
        # AWS parameters
        profile: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> bool:
        """Upload a file to remote host via AWS SSM.

        Args:
            local_path: Path to local file
            remote_path: Destination path on remote host
            target: EC2 instance or managed instance ID
            progress_callback: Optional callback for progress updates
            verify_checksum: Whether to verify file integrity
            chunk_size: Transfer chunk size in bytes
            profile: AWS profile name
            region: AWS region
            endpoint_url: Custom AWS endpoint URL

        Returns:
            True if transfer successful, False otherwise
        """
        options = FileTransferOptions(
            chunk_size=chunk_size,
            verify_checksum=verify_checksum,
            progress_callback=progress_callback,
        )

        client = FileTransferClient()
        return await client.upload_file(
            local_path=local_path,
            remote_path=remote_path,
            target=target,
            options=options,
            profile=profile,
            region=region,
            endpoint_url=endpoint_url,
        )

    async def download_file(
        self,
        remote_path: str,
        local_path: str,
        target: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        verify_checksum: bool = True,
        chunk_size: int = 32768,
        # AWS parameters
        profile: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> bool:
        """Download a file from remote host via AWS SSM.

        Args:
            remote_path: Path to remote file
            local_path: Local destination path
            target: EC2 instance or managed instance ID
            progress_callback: Optional callback for progress updates
            verify_checksum: Whether to verify file integrity
            chunk_size: Transfer chunk size in bytes
            profile: AWS profile name
            region: AWS region
            endpoint_url: Custom AWS endpoint URL

        Returns:
            True if transfer successful, False otherwise
        """
        options = FileTransferOptions(
            chunk_size=chunk_size,
            verify_checksum=verify_checksum,
            progress_callback=progress_callback,
        )

        client = FileTransferClient()
        return await client.download_file(
            remote_path=remote_path,
            local_path=local_path,
            target=target,
            options=options,
            profile=profile,
            region=region,
            endpoint_url=endpoint_url,
        )

    async def verify_remote_file(
        self,
        remote_path: str,
        target: str,
        checksum_type: str = "md5",
        # AWS parameters
        profile: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> Optional[str]:
        """Get checksum of remote file.

        Args:
            remote_path: Path to remote file
            target: EC2 instance or managed instance ID
            checksum_type: Checksum algorithm (md5 or sha256)
            profile: AWS profile name
            region: AWS region
            endpoint_url: Custom AWS endpoint URL

        Returns:
            Checksum string if successful, None otherwise
        """
        checksum_enum = (
            ChecksumType.MD5 if checksum_type.lower() == "md5" else ChecksumType.SHA256
        )

        client = FileTransferClient()
        return await client.verify_remote_file(
            remote_path=remote_path,
            target=target,
            checksum_type=checksum_enum,
            profile=profile,
            region=region,
            endpoint_url=endpoint_url,
        )
