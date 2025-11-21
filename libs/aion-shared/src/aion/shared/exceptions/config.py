from pathlib import Path
from typing import Optional


class ConfigurationError(Exception):
    """Custom exception for configuration errors with readable messages."""

    def __init__(self, message: str, details: Optional[str] = None, file_path: Optional[Path] = None):
        self.message = message
        self.details = details
        self.file_path = file_path

        full_message = message
        if file_path:
            full_message = f"Configuration error in {file_path}: {message}"
        if details:
            full_message += f"\nDetails: {details}"

        super().__init__(full_message)
