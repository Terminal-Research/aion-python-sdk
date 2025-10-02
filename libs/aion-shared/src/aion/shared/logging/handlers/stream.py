import logging
from datetime import datetime

from aion.shared.logging.base import AionLogRecord
from aion.shared.settings import app_settings
from aion.shared.utils.text import colorize_text


class LogStreamFormatter(logging.Formatter):
    """
    Basic formatter for stream output with standard format. Adds colors to log messages based on level
    """
    COLOR_ALIASES = {
        "DEBUG": "light_grey",
        "INFO": "bright_grey",
        "WARNING": "orange",
        "ERROR": "red",
        "CRITICAL": "bright_red",
    }

    def format(self, record: AionLogRecord):
        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]

        # Build the formatted message manually
        if app_settings.agent_id:
            formatted_message = f"{timestamp} - {record.levelname} - {record.name} - Agent [{app_settings.agent_id}] - {record.getMessage()}"
        else:
            formatted_message = f"{timestamp} - {record.levelname} - {record.name} - {record.getMessage()}"

        return self._colorize(record.levelname, formatted_message)

    def _colorize(self, level: str, message: str):
        try:
            color_alias = self.COLOR_ALIASES.get(level, self.COLOR_ALIASES["INFO"])
            return colorize_text(text=message, color=color_alias)
        except Exception:
            return message


class LogStreamHandler(logging.StreamHandler):
    """
    Custom StreamHandler that includes context information in log output
    """

    def __init__(self, stream=None):
        super().__init__(stream)
        self.setFormatter(LogStreamFormatter())
