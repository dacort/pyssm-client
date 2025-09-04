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

        try:
            # Create AWS SSM session
            session_data = await self._create_ssm_session(
                target=target, profile=profile, region=region, endpoint_url=endpoint_url
            )

            # Set up data channel
            data_channel = await self._setup_data_channel(session_data)

            # Compute local checksum if verification enabled
            local_checksum = None
            if options.verify_checksum:
                local_checksum = FileChecksum.compute(local_file, options.checksum_type)
                self.logger.debug(
                    f"Local {options.checksum_type.value}: {local_checksum.value}"
                )

            # Perform upload
            success = await self._upload_file_data(
                data_channel=data_channel,
                local_file=local_file,
                remote_path=remote_path,
                options=options,
            )

            if success and options.verify_checksum and local_checksum:
                # Verify remote checksum
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

            await data_channel.close()
            return success

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

            # Set up data channel
            data_channel = await self._setup_data_channel(session_data)

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

            await data_channel.close()
            return success

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

            data_channel = await self._setup_data_channel(session_data)

            checksum = await self._get_remote_checksum(
                target=target,
                remote_path=remote_path,
                checksum_type=checksum_type,
                profile=profile,
                region=region,
                endpoint_url=endpoint_url,
            )

            await data_channel.close()
            return checksum

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
            }
        except (BotoCoreError, ClientError) as e:
            self.logger.error(f"Failed to create SSM session: {e}")
            raise

    async def _setup_data_channel(self, session_data: dict) -> Any:
        """Set up data channel for file transfer."""
        from ..communicator.data_channel import SessionDataChannel

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

        # Open connection
        success = await data_channel.open()
        if not success:
            raise RuntimeError("Failed to establish data channel")

        # Store handlers for command execution
        data_channel._received_data = received_data  # type: ignore
        data_channel._command_complete = command_complete  # type: ignore

        return data_channel

    async def _upload_file_data(
        self,
        data_channel: Any,
        local_file: Path,
        remote_path: str,
        options: FileTransferOptions,
    ) -> bool:
        """Upload file data through data channel."""
        try:
            if options.encoding == FileTransferEncoding.BASE64:
                return await self._upload_base64(
                    data_channel, local_file, remote_path, options
                )
            elif options.encoding == FileTransferEncoding.RAW:
                return await self._upload_raw(
                    data_channel, local_file, remote_path, options
                )
            elif options.encoding == FileTransferEncoding.UUENCODE:
                return await self._upload_uuencode(
                    data_channel, local_file, remote_path, options
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
    ) -> bool:
        """Upload file using base64 encoding."""
        temp_remote = f"{remote_path}{options.temp_suffix}"

        # Start base64 decode process on remote using a here-doc to delimit input
        # This avoids relying on Ctrl-D/EOF semantics which can vary by TTY mode
        # and ensures the decoder receives the full payload before exiting.
        heredoc_tag = "__SSM_EOF__"
        decode_cmd = f"cat <<'{heredoc_tag}' | base64 -d > '{temp_remote}'\n"
        await data_channel.send_input_data(decode_cmd.encode())

        # Read and encode file in chunks
        bytes_sent = 0
        file_size = local_file.stat().st_size

        with open(local_file, "rb") as f:
            while chunk := f.read(options.chunk_size):
                # Send base64 encoded data; add newline to keep reasonable line lengths
                # Newlines/CRs are ignored by base64 -d.
                encoded_chunk = base64.b64encode(chunk) + b"\n"
                await data_channel.send_input_data(encoded_chunk)

                bytes_sent += len(chunk)

                # Progress callback
                if options.progress_callback:
                    options.progress_callback(bytes_sent, file_size)

                # Small delay to avoid overwhelming remote
                await asyncio.sleep(0.001)

        # Close the here-doc to signal end of data to the remote shell
        await data_channel.send_input_data((heredoc_tag + "\n").encode())

        # Wait a moment for base64 to complete processing, then move file
        await asyncio.sleep(0.5)

        # Move temp file to final location
        move_cmd = f"mv '{temp_remote}' '{remote_path}'"
        await data_channel.send_input_data(f"{move_cmd}\n".encode())
        await asyncio.sleep(0.2)  # Wait for move to complete

        return True

    async def _upload_raw(
        self,
        data_channel: Any,
        local_file: Path,
        remote_path: str,
        options: FileTransferOptions,
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
    ) -> bool:
        """Upload file using uuencoding."""
        raise NotImplementedError("Uuencode upload not yet implemented")

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
        self, target: str, remote_path: str, checksum_type: ChecksumType, **aws_kwargs: Any
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
