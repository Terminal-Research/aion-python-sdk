import logging

from aion.shared.logging.base import AionLogRecord
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

    def __init__(self):
        super().__init__('%(asctime)s - %(levelname)s - %(name)s -  %(message)s')

    def format(self, record: AionLogRecord):
        formatted_message = super().format(record)

        try:
            color_alias = self.COLOR_ALIASES.get(record.levelname, self.COLOR_ALIASES["INFO"])
            return colorize_text(text=formatted_message, color=color_alias)
        except Exception:
            return formatted_message


class LogStreamHandler(logging.StreamHandler):
    """
    Custom StreamHandler that includes context information in log output
    """

    def __init__(self, stream=None):
        super().__init__(stream)
        self.setFormatter(LogStreamFormatter())
