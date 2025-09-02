"""File transfer module for binary file operations over AWS SSM."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import FileTransferClient
    from .types import FileTransferDirection, FileTransferEncoding, FileTransferOptions

__all__ = [
    "FileTransferClient",
    "FileTransferDirection", 
    "FileTransferEncoding",
    "FileTransferOptions",
]


def __getattr__(name: str):
    """Lazy import for file transfer components."""
    if name == "FileTransferClient":
        from .client import FileTransferClient
        return FileTransferClient
    elif name in ("FileTransferDirection", "FileTransferEncoding", "FileTransferOptions"):
        from .types import FileTransferDirection, FileTransferEncoding, FileTransferOptions
        return locals()[name]
    else:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
