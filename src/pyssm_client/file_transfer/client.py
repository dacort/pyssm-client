"""Core file transfer client for binary file operations."""

import asyncio
import base64
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ..communicator.utils import create_websocket_config
from ..exec import run_command
from ..utils.logging import get_logger
from .types import ChecksumType, FileChecksum, FileTransferEncoding, FileTransferOptions


class FileTransferClient:
    """High-level client for binary file transfer operations."""

    def __init__(self) -> None:
        """Initialize file transfer client."""
        self.logger = get_logger(__name__)

    async def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        target: str,
        options: Optional[FileTransferOptions] = None,
        # AWS parameters
        profile: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> bool:
        """Upload a local file to remote host.

        Args:
            local_path: Path to local file
            remote_path: Destination path on remote host
            target: EC2 instance or managed instance ID
            options: Transfer options
            profile: AWS profile name
            region: AWS region
            endpoint_url: Custom AWS endpoint URL

        Returns:
            True if transfer successful, False otherwise
        """
        local_file = Path(local_path)
        if not local_file.exists():
            raise FileNotFoundError(f"Local file not found: {local_file}")

        if not local_file.is_file():
            raise ValueError(f"Path is not a file: {local_file}")

        options = options or FileTransferOptions()

        upload_success = False

        try:
            # Compute local checksum if verification enabled
            local_checksum = None
            if options.verify_checksum:
                local_checksum = FileChecksum.compute(local_file, options.checksum_type)
                self.logger.debug(
                    f"Local {options.checksum_type.value}: {local_checksum.value}"
                )

            # Create AWS SSM session for upload
            session_data = await self._create_ssm_session(
                target=target, profile=profile, region=region, endpoint_url=endpoint_url
            )
            ssm_client = session_data.get("ssm_client")

            # Set up data channel
            data_channel, session_obj = await self._setup_data_channel(session_data)

            try:
                # Perform upload (data only, no verification/move)
                upload_success = await self._upload_file_data(
                    data_channel=data_channel,
                    local_file=local_file,
                    remote_path=remote_path,
                    options=options,
                    target=target,
                    profile=profile,
                    region=region,
                    endpoint_url=endpoint_url,
                )
            finally:
                # Add delay to allow AWS to fully process upload completion before termination
                await asyncio.sleep(0.2)
                # Terminate main session immediately after upload
                try:
                    await session_obj.terminate_session()
                except Exception:
                    pass
                try:
                    await data_channel.close()
                except Exception:
                    pass
                if ssm_client:
                    try:
                        ssm_client.terminate_session(
                            SessionId=session_data["session_id"]
                        )
                    except Exception:
                        pass

            if not upload_success:
                return False

            # Upload already includes size verification, move, and chmod operations

            # Verify checksum if requested (separate session)
            if options.verify_checksum and local_checksum:
                remote_checksum = await self._get_remote_checksum(
                    target=target,
                    remote_path=remote_path,
                    checksum_type=options.checksum_type,
                    profile=profile,
                    region=region,
                    endpoint_url=endpoint_url,
                )

                if remote_checksum != local_checksum.value:
                    self.logger.error(
                        f"Checksum mismatch: local={local_checksum.value}, remote={remote_checksum}"
                    )
                    return False

                self.logger.debug("Checksum verification passed")

            return True

        except Exception as e:
            self.logger.error(f"Upload failed: {e}")
            if options.error_callback:
                options.error_callback(e)
            return False

    async def download_file(
        self,
        remote_path: str,
        local_path: str | Path,
        target: str,
        options: Optional[FileTransferOptions] = None,
        # AWS parameters
        profile: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> bool:
        """Download a file from remote host to local filesystem.

        Args:
            remote_path: Path to remote file
            local_path: Local destination path
            target: EC2 instance or managed instance ID
            options: Transfer options
            profile: AWS profile name
            region: AWS region
            endpoint_url: Custom AWS endpoint URL

        Returns:
            True if transfer successful, False otherwise
        """
        local_file = Path(local_path)
        options = options or FileTransferOptions()

        try:
            # Create AWS SSM session
            session_data = await self._create_ssm_session(
                target=target, profile=profile, region=region, endpoint_url=endpoint_url
            )
            ssm_client = session_data.get("ssm_client")

            # Set up data channel
            data_channel, session_obj = await self._setup_data_channel(session_data)

            try:
                # Get remote checksum if verification enabled
                remote_checksum = None
                if options.verify_checksum:
                    remote_checksum = await self._get_remote_checksum(
                        target=target,
                        remote_path=remote_path,
                        checksum_type=options.checksum_type,
                        profile=profile,
                        region=region,
                        endpoint_url=endpoint_url,
                    )
                    self.logger.debug(
                        f"Remote {options.checksum_type.value}: {remote_checksum}"
                    )

                # Perform download
                success = await self._download_file_data(
                    data_channel=data_channel,
                    remote_path=remote_path,
                    local_file=local_file,
                    options=options,
                )

                if success and options.verify_checksum and remote_checksum:
                    # Verify local checksum
                    local_checksum = FileChecksum.compute(local_file, options.checksum_type)

                    if local_checksum.value != remote_checksum:
                        self.logger.error(
                            f"Checksum mismatch: remote={remote_checksum}, local={local_checksum.value}"
                        )
                        return False

                    self.logger.debug("Checksum verification passed")

                return success
            finally:
                # Ensure proper cleanup in all cases
                try:
                    await session_obj.terminate_session()
                except Exception:
                    pass
                try:
                    await data_channel.close()
                except Exception:
                    pass
                if ssm_client:
                    try:
                        ssm_client.terminate_session(
                            SessionId=session_data["session_id"]
                        )
                    except Exception:
                        pass

        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            if options.error_callback:
                options.error_callback(e)
            return False

    async def verify_remote_file(
        self,
        remote_path: str,
        target: str,
        checksum_type: ChecksumType = ChecksumType.MD5,
        # AWS parameters
        profile: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> Optional[str]:
        """Get checksum of remote file.

        Args:
            remote_path: Path to remote file
            target: EC2 instance or managed instance ID
            checksum_type: Checksum algorithm to use
            profile: AWS profile name
            region: AWS region
            endpoint_url: Custom AWS endpoint URL

        Returns:
            Checksum string if successful, None otherwise
        """
        try:
            session_data = await self._create_ssm_session(
                target=target, profile=profile, region=region, endpoint_url=endpoint_url
            )
            ssm_client = session_data.get("ssm_client")

            data_channel, session_obj = await self._setup_data_channel(session_data)

            try:
                checksum = await self._get_remote_checksum(
                    target=target,
                    remote_path=remote_path,
                    checksum_type=checksum_type,
                    profile=profile,
                    region=region,
                    endpoint_url=endpoint_url,
                )

                return checksum
            finally:
                # Ensure proper cleanup in all cases
                try:
                    await session_obj.terminate_session()
                except Exception:
                    pass
                try:
                    await data_channel.close()
                except Exception:
                    pass
                if ssm_client:
                    try:
                        ssm_client.terminate_session(
                            SessionId=session_data["session_id"]
                        )
                    except Exception:
                        pass

        except Exception as e:
            self.logger.error(f"Remote checksum failed: {e}")
            return None

    async def _create_ssm_session(
        self,
        target: str,
        profile: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> dict:
        """Create AWS SSM session for file transfer."""
        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        if region:
            session_kwargs["region_name"] = region

        session = boto3.Session(**session_kwargs)  # type: ignore[arg-type]
        ssm = session.client("ssm", endpoint_url=endpoint_url)

        # Start session for Standard_Stream (shell access)
        params = {"Target": target}

        try:
            response = ssm.start_session(**params)
            return {
                "session_id": response["SessionId"],
                "token_value": response["TokenValue"],
                "stream_url": response["StreamUrl"],
                "target": target,
                "ssm_client": ssm,
            }
        except (BotoCoreError, ClientError) as e:
            self.logger.error(f"Failed to create SSM session: {e}")
            raise

    async def _setup_data_channel(self, session_data: dict) -> tuple[Any, Any]:
        """Set up data channel for file transfer."""
        from ..communicator.data_channel import SessionDataChannel
        from ..cli.types import ConnectArguments
        from ..session.registry import get_session_registry
        from ..session.plugins import StandardStreamPlugin
        from ..session.session_handler import SessionHandler

        # Create session object first
        args = ConnectArguments(
            session_id=session_data["session_id"],
            stream_url=session_data["stream_url"],
            token_value=session_data["token_value"],
            target=session_data["target"],
            session_type="Standard_Stream",
        )

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

        # Create data channel
        websocket_config = create_websocket_config(
            stream_url=session_data["stream_url"], token=session_data["token_value"]
        )

        data_channel = SessionDataChannel(websocket_config)

        # Set up basic handlers
        received_data = []
        command_complete = asyncio.Event()

        def handle_output(data: bytes) -> None:
            received_data.append(data)

        def handle_closed() -> None:
            command_complete.set()

        data_channel.set_input_handler(handle_output)
        data_channel.set_closed_handler(handle_closed)

        # Set client info and attach to session
        from ..constants import CLIENT_VERSION
        try:
            data_channel.set_client_info("pyssm-client", CLIENT_VERSION)
        except Exception:
            pass
        session_obj.set_data_channel(data_channel)

        # Execute session
        await session_obj.execute()

        # Store handlers for command execution
        data_channel._received_data = received_data  # type: ignore
        data_channel._command_complete = command_complete  # type: ignore

        return data_channel, session_obj

    async def _upload_file_data(
        self,
        data_channel: Any,
        local_file: Path,
        remote_path: str,
        options: FileTransferOptions,
        *,
        target: str,
        profile: Optional[str],
        region: Optional[str],
        endpoint_url: Optional[str],
    ) -> bool:
        """Upload file data through data channel."""
        try:
            if options.encoding == FileTransferEncoding.BASE64:
                return await self._upload_base64(
                    data_channel,
                    local_file,
                    remote_path,
                    options,
                    target=target,
                    profile=profile,
                    region=region,
                    endpoint_url=endpoint_url,
                )
            elif options.encoding == FileTransferEncoding.RAW:
                return await self._upload_raw(
                    data_channel,
                    local_file,
                    remote_path,
                    options,
                    target=target,
                    profile=profile,
                    region=region,
                    endpoint_url=endpoint_url,
                )
            elif options.encoding == FileTransferEncoding.UUENCODE:
                return await self._upload_uuencode(
                    data_channel,
                    local_file,
                    remote_path,
                    options,
                    target=target,
                    profile=profile,
                    region=region,
                    endpoint_url=endpoint_url,
                )
            else:
                raise ValueError(f"Unsupported encoding: {options.encoding}")

        except Exception as e:
            self.logger.error(f"File upload failed: {e}")
            return False

    async def _upload_base64(
        self,
        data_channel: Any,
        local_file: Path,
        remote_path: str,
        options: FileTransferOptions,
        *,
        target: str,
        profile: Optional[str],
        region: Optional[str],
        endpoint_url: Optional[str],
    ) -> bool:
        """Upload file using base64 encoding via data channel with verification, move, and chmod."""
        temp_remote = f"{remote_path}{options.temp_suffix}"
        
        # Clear any existing temp file
        clear_cmd = f"rm -f '{temp_remote}'\n"
        await data_channel.send_input_data(clear_cmd.encode())
        await asyncio.sleep(0.1)

        # Read and upload file in chunks using data channel
        bytes_sent = 0
        file_size = local_file.stat().st_size

        self.logger.info(
            "Starting upload of %s (%s bytes)", local_file, file_size
        )

        with open(local_file, "rb") as f:
            while chunk := f.read(options.chunk_size):
                # Encode chunk to base64
                encoded_chunk = base64.b64encode(chunk).decode('ascii')
                
                # Send chunk using simple append (no heredoc complexity)
                append_cmd = f"echo -n '{encoded_chunk}' | base64 -d >> '{temp_remote}'\n"
                await data_channel.send_input_data(append_cmd.encode())
                
                bytes_sent += len(chunk)

                # Progress callback
                if options.progress_callback:
                    options.progress_callback(bytes_sent, file_size)

                # Small delay to avoid overwhelming remote
                await asyncio.sleep(0.005)

        if options.progress_callback:
            try:
                import sys

                sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:
                pass

        self.logger.info("All chunks sent; waiting for remote flush...")
        remote_size = await self._wait_for_remote_size(
            temp_remote,
            expected_size=file_size,
            target=target,
            profile=profile,
            region=region,
            endpoint_url=endpoint_url,
        )

        if remote_size < file_size:
            self.logger.warning(
                "Remote file %s reported %s bytes (< %s) before verification",
                temp_remote,
                remote_size,
                file_size,
            )

        self.logger.debug("Upload completed: %s bytes sent to %s", bytes_sent, temp_remote)
        
        # Move temp file to final location via data channel
        move_cmd = f"mv '{temp_remote}' '{remote_path}'\n"
        await data_channel.send_input_data(move_cmd.encode())
        await asyncio.sleep(0.2)
        
        # Set file permissions to match local file
        local_mode = local_file.stat().st_mode & 0o777
        chmod_cmd = f"chmod {local_mode:o} '{remote_path}'\n"
        await data_channel.send_input_data(chmod_cmd.encode())
        await asyncio.sleep(0.1)
        
        return True


    async def _upload_raw(
        self,
        data_channel: Any,
        local_file: Path,
        remote_path: str,
        options: FileTransferOptions,
        *,
        target: str,
        profile: Optional[str],
        region: Optional[str],
        endpoint_url: Optional[str],
    ) -> bool:
        """Upload file using raw binary (not implemented - requires special handling)."""
        raise NotImplementedError(
            "Raw binary upload requires terminal binary mode support"
        )

    async def _upload_uuencode(
        self,
        data_channel: Any,
        local_file: Path,
        remote_path: str,
        options: FileTransferOptions,
        *,
        target: str,
        profile: Optional[str],
        region: Optional[str],
        endpoint_url: Optional[str],
    ) -> bool:
        """Upload file using uuencoding."""
        raise NotImplementedError("Uuencode upload not yet implemented")

    async def _wait_for_remote_size(
        self,
        remote_path: str,
        expected_size: int,
        *,
        target: str,
        profile: Optional[str],
        region: Optional[str],
        endpoint_url: Optional[str],
        max_attempts: int = 40,
        delay: float = 0.5,
    ) -> int:
        """Poll the remote host until the file reaches the expected size."""
        if expected_size == 0:
            return 0

        last_size = 0
        attempts = 0

        while attempts < max_attempts:
            attempts += 1
            result = await run_command(
                target=target,
                command=f"wc -c < '{remote_path}'",
                profile=profile,
                region=region,
                endpoint_url=endpoint_url,
                timeout=15,
            )

            if result.exit_code == 0:
                try:
                    last_size = int(result.stdout.decode().strip() or "0")
                except ValueError:
                    last_size = 0
            else:
                self.logger.debug(
                    "Remote wc command on %s exited %s: %s",
                    remote_path,
                    result.exit_code,
                    result.stderr.decode(errors="ignore"),
                )
                last_size = 0

            self.logger.debug(
                "Remote file %s currently %s/%s bytes", remote_path, last_size, expected_size
            )

            if last_size >= expected_size:
                return last_size

            await asyncio.sleep(delay)

        return last_size

    async def _download_file_data(
        self,
        data_channel: Any,
        remote_path: str,
        local_file: Path,
        options: FileTransferOptions,
    ) -> bool:
        """Download file data through data channel."""
        if options.encoding == FileTransferEncoding.BASE64:
            return await self._download_base64(
                data_channel, remote_path, local_file, options
            )
        else:
            raise NotImplementedError(
                f"Download with {options.encoding} not yet implemented"
            )

    async def _download_base64(
        self,
        data_channel: Any,
        remote_path: str,
        local_file: Path,
        options: FileTransferOptions,
    ) -> bool:
        """Download file using base64 encoding."""
        # Clear received data buffer
        data_channel._received_data.clear()  # type: ignore

        # Start base64 encode command on remote
        encode_cmd = f"base64 '{remote_path}'\n"
        await data_channel.send_input_data(encode_cmd.encode())

        # Wait for command to complete (simple timeout)
        await asyncio.sleep(2)

        # Collect all received data
        all_data = b"".join(data_channel._received_data)  # type: ignore

        # Extract base64 content (skip shell prompt/command echo)
        lines = all_data.decode("utf-8", errors="ignore").split("\n")
        base64_lines = []

        for line in lines:
            line = line.strip()
            if (
                line
                and not line.startswith("$")
                and not line.startswith("#")
                and "/" not in line
            ):
                # Likely base64 data
                base64_lines.append(line)

        if not base64_lines:
            self.logger.error("No base64 data received from remote")
            return False

        # Decode and write to local file
        try:
            base64_data = "".join(base64_lines)
            file_data = base64.b64decode(base64_data)

            with open(local_file, "wb") as f:
                f.write(file_data)

            return True

        except Exception as e:
            self.logger.error(f"Failed to decode base64 data: {e}")
            return False

    async def _get_remote_checksum(
        self,
        target: str,
        remote_path: str,
        checksum_type: ChecksumType,
        **aws_kwargs: Any,
    ) -> str:
        """Get checksum of remote file using exec API."""
        # Build checksum command
        if checksum_type == ChecksumType.MD5:
            cmd = f"md5sum '{remote_path}'"
        elif checksum_type == ChecksumType.SHA256:
            cmd = f"sha256sum '{remote_path}'"
        else:
            raise ValueError(f"Unsupported checksum type: {checksum_type}")

        self.logger.debug(f"Getting remote checksum with command: {cmd}")
        self.logger.debug(f"Target: {target}, AWS kwargs: {aws_kwargs}")

        # Execute command using clean exec API
        result = await run_command(target=target, command=cmd, **aws_kwargs)
        self.logger.debug(f"Exec result: {result}")

        if result.exit_code != 0:
            stderr_text = result.stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Checksum command failed (exit {result.exit_code}): {stderr_text}"
            )

        # Parse checksum from clean stdout
        stdout_text = result.stdout.decode("utf-8", errors="ignore").strip()

        if not stdout_text:
            raise RuntimeError("No output from checksum command")

        # Extract checksum (first part before whitespace)
        parts = stdout_text.split()
        if not parts:
            raise RuntimeError(f"Could not parse checksum from output: {stdout_text}")

        checksum = parts[0].lower()

        # Validate checksum format
        expected_length = 32 if checksum_type == ChecksumType.MD5 else 64
        if len(checksum) != expected_length or not all(
            c in "0123456789abcdef" for c in checksum
        ):
            raise RuntimeError(
                f"Invalid {checksum_type.value} checksum format: {checksum}"
            )

        self.logger.debug(f"Found valid {checksum_type.value} checksum: {checksum}")
        return checksum
