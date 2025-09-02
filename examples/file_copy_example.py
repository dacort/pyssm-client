#!/usr/bin/env python3
"""Example showing how to use the file transfer functionality."""

import asyncio
from pathlib import Path

from session_manager_plugin.cli.main import SessionManagerPlugin
from session_manager_plugin.file_transfer.client import FileTransferClient
from session_manager_plugin.file_transfer.types import FileTransferOptions


async def example_programmatic_api():
    """Example using the programmatic API through SessionManagerPlugin."""
    
    # Create a SessionManagerPlugin instance
    plugin = SessionManagerPlugin()
    
    # Define progress callback
    def show_progress(bytes_transferred: int, total_bytes: int) -> None:
        percentage = (bytes_transferred / total_bytes) * 100 if total_bytes > 0 else 0
        print(f"Progress: {bytes_transferred}/{total_bytes} bytes ({percentage:.1f}%)")
    
    try:
        # Example 1: Upload a file
        print("Uploading file...")
        success = await plugin.upload_file(
            local_path="./example.txt",
            remote_path="/tmp/example.txt",
            target="i-1234567890abcdef0",
            progress_callback=show_progress,
            verify_checksum=True
        )
        print(f"Upload {'succeeded' if success else 'failed'}")
        
        # Example 2: Download a file
        print("\nDownloading file...")
        success = await plugin.download_file(
            remote_path="/var/log/messages",
            local_path="./downloaded_messages.log",
            target="i-1234567890abcdef0",
            progress_callback=show_progress,
            verify_checksum=True
        )
        print(f"Download {'succeeded' if success else 'failed'}")
        
        # Example 3: Verify remote file
        print("\nVerifying remote file...")
        checksum = await plugin.verify_remote_file(
            remote_path="/tmp/example.txt",
            target="i-1234567890abcdef0",
            checksum_type="md5"
        )
        print(f"Remote file MD5: {checksum}")
        
    except Exception as e:
        print(f"Error: {e}")


async def example_direct_client():
    """Example using FileTransferClient directly."""
    
    client = FileTransferClient()
    
    # Custom transfer options
    options = FileTransferOptions(
        chunk_size=32768,  # 32KB chunks
        verify_checksum=True,
        progress_callback=lambda sent, total: print(f"Transferred {sent}/{total} bytes")
    )
    
    try:
        # Upload with custom options
        success = await client.upload_file(
            local_path="./document.pdf",
            remote_path="/tmp/document.pdf", 
            target="i-1234567890abcdef0",
            options=options,
            profile="my-aws-profile",  # Use specific AWS profile
            region="us-west-2"        # Use specific region
        )
        print(f"Upload with custom options: {'succeeded' if success else 'failed'}")
        
    except Exception as e:
        print(f"Error: {e}")


def cli_examples():
    """Show CLI usage examples."""
    print("CLI Usage Examples:")
    print("==================")
    print()
    print("# Upload a file")
    print("session-manager-plugin copy --target i-1234567890abcdef0 --local-path ./file.txt --remote-path /tmp/file.txt")
    print()
    print("# Download a file")
    print("session-manager-plugin copy --target i-1234567890abcdef0 --remote-path /var/log/app.log --local-path ./app.log")
    print()
    print("# Auto-detect direction (upload)")
    print("session-manager-plugin copy --target i-1234567890abcdef0 --local-path ./document.pdf")
    print()
    print("# Auto-detect direction (download)")
    print("session-manager-plugin copy --target i-1234567890abcdef0 --remote-path /etc/hosts")
    print()
    print("# Use different encoding and chunk size")
    print("session-manager-plugin copy --target i-1234567890abcdef0 --local-path ./large_file.zip --remote-path /tmp/large_file.zip --encoding base64 --chunk-size 131072")
    print()
    print("# Skip checksum verification for faster transfer")
    print("session-manager-plugin copy --target i-1234567890abcdef0 --local-path ./file.txt --remote-path /tmp/file.txt --no-verify")
    print()
    print("# Quiet mode")
    print("session-manager-plugin copy --target i-1234567890abcdef0 --local-path ./file.txt --remote-path /tmp/file.txt --quiet")


if __name__ == "__main__":
    print("File Transfer Examples")
    print("======================")
    
    # Show CLI examples
    cli_examples()
    
    print("\nTo run programmatic examples (requires AWS credentials and valid instance):")
    print("- Uncomment the lines below")
    print("- Replace 'i-1234567890abcdef0' with a real instance ID")
    print("- Ensure AWS credentials are configured")
    
    # Uncomment to run actual examples (requires AWS setup)
    # asyncio.run(example_programmatic_api())
    # asyncio.run(example_direct_client())