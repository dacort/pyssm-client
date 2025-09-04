"""Main CLI interface for AWS Session Manager Plugin."""

import asyncio
import json
import logging
import sys
from typing import Any, Dict

import click
import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ..exec import run_command_sync
from ..file_transfer.client import FileTransferClient
from ..file_transfer.types import (
    ChecksumType,
    FileTransferEncoding,
    FileTransferOptions,
)
from ..utils.logging import setup_logging
from .coordinator import SessionManagerPlugin
from .types import ConnectArguments, SSHArguments, FileCopyArguments


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
@click.option(
    "--target", required=True, help="Target EC2 instance or managed instance ID"
)
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

        session = boto3.Session(**session_kwargs)  # type: ignore[arg-type]
        ssm = session.client("ssm", endpoint_url=ssh_args.endpoint_url)

        # Build start_session parameters
        params: Dict[str, Any] = {"Target": ssh_args.target}
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


@cli.command()
@click.argument("source")
@click.argument("destination")
@click.option(
    "--encoding",
    type=click.Choice(["base64", "raw", "uuencode"], case_sensitive=False),
    default="base64",
    show_default=True,
    help="Transfer encoding method",
)
@click.option(
    "--chunk-size",
    type=int,
    default=65536,
    show_default=True,
    help="Transfer chunk size in bytes",
)
@click.option("--no-verify", is_flag=True, help="Skip checksum verification")
@click.option(
    "--checksum-type",
    type=click.Choice(["md5", "sha256"], case_sensitive=False),
    default="md5",
    show_default=True,
    help="Checksum algorithm for verification",
)
@click.option("--profile", help="AWS profile")
@click.option("--region", help="AWS region")
@click.option("--endpoint-url", help="AWS endpoint URL")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output")
@click.option("--no-progress", is_flag=True, help="Disable progress bar")
@click.pass_context
def copy(ctx: click.Context, source: str, destination: str, **kwargs: Any) -> None:
    """
    Copy files to/from remote hosts via AWS SSM using scp-like syntax.

    Use TARGET:PATH for remote files and local paths for local files.

    Examples:
      # Upload a file (local to remote)
      session-manager-plugin copy ./file.txt i-1234567890abcdef0:/tmp/file.txt

      # Download a file (remote to local)
      session-manager-plugin copy i-1234567890abcdef0:/var/log/app.log ./app.log

      # Upload to remote home directory
      session-manager-plugin copy ./document.pdf i-1234567890abcdef0:~/document.pdf

      # Download with different local name
      session-manager-plugin copy i-1234567890abcdef0:/etc/hosts ./remote_hosts
    """
    try:
        # Parse scp-style arguments
        filtered_kwargs = {
            k.replace("-", "_"): v for k, v in kwargs.items() if v is not None
        }

        # Convert string enums to enum types
        if "encoding" in filtered_kwargs:
            encoding_map = {
                "base64": FileTransferEncoding.BASE64,
                "raw": FileTransferEncoding.RAW,
                "uuencode": FileTransferEncoding.UUENCODE,
            }
            filtered_kwargs["encoding"] = encoding_map[
                filtered_kwargs["encoding"].lower()
            ]
        if "checksum_type" in filtered_kwargs:
            checksum_map = {"md5": ChecksumType.MD5, "sha256": ChecksumType.SHA256}
            filtered_kwargs["checksum_type"] = checksum_map[
                filtered_kwargs["checksum_type"].lower()
            ]

        # Handle verify flag
        filtered_kwargs["verify_checksum"] = not filtered_kwargs.pop("no_verify", False)
        filtered_kwargs["show_progress"] = not (
            filtered_kwargs.pop("quiet", False)
            or filtered_kwargs.pop("no_progress", False)
        )

        # Create FileCopyArguments using scp-style parsing
        copy_args = FileCopyArguments.from_scp_style(
            source, destination, **filtered_kwargs
        )

        # Validate arguments
        errors = copy_args.validate()
        if errors:
            for error in errors:
                click.echo(f"Error: {error}", err=True)
            sys.exit(1)

        # Set up progress callback if enabled
        progress_callback = None
        if copy_args.show_progress and not copy_args.quiet:

            def show_progress(bytes_transferred: int, total_bytes: int) -> None:
                if total_bytes > 0:
                    percentage = (bytes_transferred / total_bytes) * 100
                    click.echo(
                        f"\rProgress: {bytes_transferred}/{total_bytes} bytes ({percentage:.1f}%)",
                        nl=False,
                    )

            progress_callback = show_progress

        # Create transfer options
        options = FileTransferOptions(
            chunk_size=copy_args.chunk_size,
            encoding=copy_args.encoding,
            verify_checksum=copy_args.verify_checksum,
            checksum_type=copy_args.checksum_type,
            progress_callback=progress_callback,
        )

        # Execute file transfer
        client = FileTransferClient()

        async def run_transfer() -> bool:
            if copy_args.is_upload:
                if (
                    not copy_args.local_path
                    or not copy_args.remote_path
                    or not copy_args.target
                ):
                    click.echo(
                        "Error: Upload requires valid local_path, remote_path, and target",
                        err=True,
                    )
                    return False
                click.echo(
                    f"Uploading {copy_args.local_path} to {copy_args.target}:{copy_args.remote_path}"
                )
                return await client.upload_file(
                    local_path=copy_args.local_path,
                    remote_path=copy_args.remote_path,
                    target=copy_args.target,
                    options=options,
                    profile=copy_args.profile,
                    region=copy_args.region,
                    endpoint_url=copy_args.endpoint_url,
                )
            else:  # download
                if (
                    not copy_args.remote_path
                    or not copy_args.local_path
                    or not copy_args.target
                ):
                    click.echo(
                        "Error: Download requires valid remote_path, local_path, and target",
                        err=True,
                    )
                    return False
                click.echo(
                    f"Downloading {copy_args.target}:{copy_args.remote_path} to {copy_args.local_path}"
                )
                return await client.download_file(
                    remote_path=copy_args.remote_path,
                    local_path=copy_args.local_path,
                    target=copy_args.target,
                    options=options,
                    profile=copy_args.profile,
                    region=copy_args.region,
                    endpoint_url=copy_args.endpoint_url,
                )

        success = asyncio.run(run_transfer())

        if success:
            if copy_args.show_progress:
                click.echo()  # New line after progress
            click.echo("Transfer completed successfully")
            sys.exit(0)
        else:
            click.echo("Transfer failed", err=True)
            sys.exit(1)

    except Exception as e:
        click.echo(f"Fatal error: {e}", err=True)
        sys.exit(1)


@cli.command(name="exec")
@click.option(
    "--target", required=True, help="Target EC2 instance or managed instance ID"
)
@click.option("--command", required=True, help="Command to execute on the target")
@click.option("--profile", help="AWS profile")
@click.option("--region", help="AWS region")
@click.option("--endpoint-url", help="AWS endpoint URL")
@click.option("--timeout", default=600, show_default=True, type=int)
def exec_command(
    target: str,
    command: str,
    profile: str | None,
    region: str | None,
    endpoint_url: str | None,
    timeout: int,
) -> None:
    """Execute a single command and return stdout/stderr/exit code."""
    try:
        result = run_command_sync(
            target=target,
            command=command,
            profile=profile,
            region=region,
            endpoint_url=endpoint_url,
            timeout=timeout,
        )
        # Print streams; exit with command exit code
        if result.stdout:
            try:
                sys.stdout.buffer.write(result.stdout)
                sys.stdout.buffer.flush()
            except Exception:
                click.echo(result.stdout.decode("utf-8", errors="replace"), nl=False)
        if result.stderr:
            try:
                sys.stderr.buffer.write(result.stderr)
                sys.stderr.buffer.flush()
            except Exception:
                click.echo(
                    result.stderr.decode("utf-8", errors="replace"), nl=False, err=True
                )
        sys.exit(result.exit_code)
    except Exception as e:
        click.echo(f"Execution failed: {e}", err=True)
        sys.exit(1)


def main() -> int:
    """Main entry point."""
    cli()
    return 0


if __name__ == "__main__":
    main()
