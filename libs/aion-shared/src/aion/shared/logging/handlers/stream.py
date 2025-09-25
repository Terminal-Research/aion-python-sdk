import logging

from aion.shared.logging.filter import ContextFilter
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

    def format(self, record):
        # Get color for this level
        levelname = record.levelname
        color_alias = self.COLOR_ALIASES.get(levelname, self.COLOR_ALIASES["INFO"])

        # Format with original formatter
        formatted_message = super().format(record)

        # Return colored version
        return colorize_text(text=formatted_message, color=color_alias)


class LogStreamHandler(logging.StreamHandler):
    """
    Custom StreamHandler that includes context information in log output
    """

    def __init__(self, stream=None):
        super().__init__(stream)
        self.addFilter(ContextFilter())
        self.setFormatter(LogStreamFormatter())
