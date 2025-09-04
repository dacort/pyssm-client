"""Programmatic single-command execution via Session Manager."""

from __future__ import annotations

# Re-export from utils.command to maintain backward compatibility
from .utils.command import CommandResult, run_command, run_command_sync

__all__ = ["CommandResult", "run_command", "run_command_sync"]
