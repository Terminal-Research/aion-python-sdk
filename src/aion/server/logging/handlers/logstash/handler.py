"""Asynchronous logging handler wiring the Aion formatter, filters and transport."""

from logstash_async.handler import AsynchronousLogstashHandler

from .filter import AionLogstashFilter
from .formatter import AionLogstashFormatter

__all__ = ["AionLogstashHandler"]


class AionLogstashHandler(AsynchronousLogstashHandler):
    """Asynchronous handler for sending logs to Logstash.

    Automatically configures the handler with AionLogstashFormatter
    and AionLogstashFilter.

    Args:
        client_id: Unique identifier for the client.
        host_name: Name of the host generating the logs.
        **kwargs: Additional arguments passed to AsynchronousLogstashHandler.
    """

    def __init__(self, client_id: str, host_name: str, **kwargs):
        from aion.server.logging.filters import ServerAionContextFilter
        super().__init__(**kwargs)
        self.setFormatter(AionLogstashFormatter(client_id=client_id, host_name=host_name))
        self.addFilter(ServerAionContextFilter())
        self.addFilter(AionLogstashFilter())
