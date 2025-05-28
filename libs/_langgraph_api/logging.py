import logging
import os
import threading

import structlog
from starlette.config import Config
from structlog.typing import EventDict

# env

log_env = Config()

LOG_JSON = log_env("LOG_JSON", cast=bool, default=False)
LOG_COLOR = log_env("LOG_COLOR", cast=bool, default=True)
LOG_LEVEL = log_env("LOG_LEVEL", cast=str, default="INFO")

logging.getLogger().setLevel(LOG_LEVEL.upper())
logging.getLogger("psycopg").setLevel(logging.WARNING)

# custom processors


def add_thread_name(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    event_dict["thread_name"] = threading.current_thread().name
    return event_dict


class AddPrefixedEnvVars:
    def __init__(self, prefix: str) -> None:
        self.kv = {
            key.removeprefix(prefix).lower(): value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }

    def __call__(
        self, logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        event_dict.update(self.kv)
        return event_dict


class JSONRenderer:
    def __call__(
        self, logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> str:
        """
        The return type of this depends on the return type of self._dumps.
        """
        from langgraph_api.serde import json_dumpb

        return json_dumpb(event_dict).decode()


LEVELS = logging.getLevelNamesMapping()


class TapForMetadata:
    def __call__(
        self, logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> str:
        """
        Tap WARN and above logs for metadata. Exclude user loggers.
        """
        if (
            event_dict["logger"].startswith("langgraph")
            and LEVELS[event_dict["level"].upper()] > LEVELS["INFO"]
        ):
            from langgraph_api.metadata import append_log

            append_log(event_dict.copy())
        return event_dict


# shared config, for both logging and structlog

shared_processors = [
    add_thread_name,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.stdlib.ExtraAdder(),
    AddPrefixedEnvVars("LANGSMITH_LANGGRAPH_"),  # injected by docker build
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
]


# configure logging, used by logging.json, applied by uvicorn

renderer = (
    JSONRenderer() if LOG_JSON else structlog.dev.ConsoleRenderer(colors=LOG_COLOR)
)


class Formatter(structlog.stdlib.ProcessorFormatter):
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


# configure structlog

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        *shared_processors,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
