"""Standard stream session plugin."""

from __future__ import annotations

from typing import List

from ..types import SessionConfig
from .base import BaseSessionPlugin


class StandardStreamPlugin(BaseSessionPlugin):
    """Plugin for Standard_Stream session type."""

    def get_supported_session_types(self) -> List[str]:
        """Return supported session types."""
        return ["Standard_Stream"]

    def validate_session_properties(self, config: SessionConfig) -> bool:
        """Validate session configuration for Standard_Stream.

        Args:
            config: Session configuration to validate

        Returns:
            True if validation passes, False otherwise
        """
        if not self._validate_basic_properties(config):
            return False

        # Standard stream sessions don't require additional validation
        self._logger.debug(
            f"Standard stream session validation passed: {config.session_id}"
        )
        return True