"""File transfer module for binary file operations over AWS SSM."""

# Only import types - they're safe and don't cause circular dependencies
from .types import ChecksumType, FileTransferDirection, FileTransferEncoding, FileTransferOptions

# Note: FileTransferClient NOT imported here to avoid circular dependency with exec module
# Import it directly from .client when needed

__all__ = [
    "FileTransferClient",
    "FileTransferDirection",
    "FileTransferEncoding",
    "FileTransferOptions",
    "ChecksumType",
]
