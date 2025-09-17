"""Types and enums for file transfer operations."""

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class FileTransferDirection(Enum):
    """Direction of file transfer."""

    UPLOAD = "upload"
    DOWNLOAD = "download"


class FileTransferEncoding(Enum):
    """Encoding method for file transfer."""

    BASE64 = "base64"
    RAW = "raw"
    UUENCODE = "uuencode"


class ChecksumType(Enum):
    """Supported checksum algorithms."""

    MD5 = "md5"
    SHA256 = "sha256"


@dataclass
class FileTransferOptions:
    """Options for file transfer operations."""

    # Transfer configuration
    chunk_size: int = (
        32768  # 32KB chunks (safe for base64 encoding + protocol overhead within 64KB frames)
    )
    encoding: FileTransferEncoding = FileTransferEncoding.BASE64
    verify_checksum: bool = True
    checksum_type: ChecksumType = ChecksumType.MD5

    # Progress and callbacks
    progress_callback: Optional[Callable[[int, int], None]] = None
    error_callback: Optional[Callable[[Exception], None]] = None

    # Remote shell options
    shell_timeout: float = 30.0  # Timeout for shell commands
    temp_suffix: str = ".tmp"  # Suffix for temporary files

    def __post_init__(self) -> None:
        """Validate options after initialization."""
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self.shell_timeout <= 0:
            raise ValueError("shell_timeout must be positive")


@dataclass
class FileTransferProgress:
    """Progress information for file transfer."""

    bytes_transferred: int
    total_bytes: int
    percentage: float
    current_chunk: int
    total_chunks: int

    @classmethod
    def create(
        cls, bytes_transferred: int, total_bytes: int, chunk_size: int
    ) -> "FileTransferProgress":
        """Create progress info from basic metrics."""
        percentage = (bytes_transferred / total_bytes * 100) if total_bytes > 0 else 0
        current_chunk = bytes_transferred // chunk_size
        total_chunks = (total_bytes + chunk_size - 1) // chunk_size

        return cls(
            bytes_transferred=bytes_transferred,
            total_bytes=total_bytes,
            percentage=percentage,
            current_chunk=current_chunk,
            total_chunks=total_chunks,
        )


@dataclass
class FileChecksum:
    """File checksum information."""

    algorithm: ChecksumType
    value: str
    file_size: int

    @classmethod
    def compute(
        cls, file_path: Path, algorithm: ChecksumType = ChecksumType.MD5
    ) -> "FileChecksum":
        """Compute checksum for a local file."""
        if algorithm == ChecksumType.MD5:
            hasher = hashlib.md5()
        elif algorithm == ChecksumType.SHA256:
            hasher = hashlib.sha256()
        else:
            raise ValueError(f"Unsupported checksum algorithm: {algorithm}")

        file_size = 0
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
                file_size += len(chunk)

        return cls(algorithm=algorithm, value=hasher.hexdigest(), file_size=file_size)
