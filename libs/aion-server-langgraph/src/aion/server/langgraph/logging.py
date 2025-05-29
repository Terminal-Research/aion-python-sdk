import logging
import os
import threading
import json

import structlog
from starlette.config import Config
from structlog.typing import EventDict

log_env = Config()

LOG_JSON = log_env("LOG_JSON", cast=bool, default=False)
LOG_COLOR = log_env("LOG_COLOR", cast=bool, default=True)
LOG_LEVEL = log_env("LOG_LEVEL", cast=str, default="INFO")

root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL.upper())
logging.getLogger("psycopg").setLevel(logging.WARNING)


def add_thread_name(logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add the current thread name to ``event_dict``."""
    event_dict["thread_name"] = threading.current_thread().name
    return event_dict


class AddPrefixedEnvVars:
    """Add environment variables with a given prefix to ``event_dict``."""

    def __init__(self, prefix: str) -> None:
        self.kv = {
            key.removeprefix(prefix).lower(): value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }

    def __call__(self, logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
        event_dict.update(self.kv)
        return event_dict


class JSONRenderer:
    """Render ``event_dict`` as JSON."""

    def __call__(self, logger: logging.Logger, method_name: str, event_dict: EventDict) -> str:
        return json.dumps(event_dict, default=str)


class TapForMetadata:
    """Placeholder for metadata tap."""

    def __call__(self, logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
        return event_dict


shared_processors = [
    add_thread_name,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.stdlib.ExtraAdder(),
    AddPrefixedEnvVars("AION_AGENT_"),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
]

renderer = JSONRenderer() if LOG_JSON else structlog.dev.ConsoleRenderer(colors=LOG_COLOR)


class Formatter(structlog.stdlib.ProcessorFormatter):
    """Formatter using structlog processors for colorful output."""

    def __init__(self, *args, **kwargs) -> None:
        if len(args) == 3:
            fmt, datefmt, style = args
            kwargs["fmt"] = fmt
            kwargs["datefmt"] = datefmt
            kwargs["style"] = style
        else:
            raise RuntimeError("Invalid number of arguments")
        super().__init__(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                TapForMetadata(),
                renderer,
            ],
            foreign_pre_chain=shared_processors,
            **kwargs,
        )


structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        *shared_processors,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(root_logger.level),
    cache_logger_on_first_use=True,
)

if not root_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(Formatter("%(message)s", None, "%"))
    root_logger.addHandler(handler)

