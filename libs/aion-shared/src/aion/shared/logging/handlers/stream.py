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

        # Add exception info if present
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)

            if record.exc_text:
                if formatted_message[-1:] != "\n":
                    formatted_message = formatted_message + "\n"
                formatted_message = formatted_message + record.exc_text

        if hasattr(record, 'stack_info') and record.stack_info:
            if formatted_message[-1:] != "\n":
                formatted_message = formatted_message + "\n"
            formatted_message = formatted_message + self.formatStack(record.stack_info)

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
