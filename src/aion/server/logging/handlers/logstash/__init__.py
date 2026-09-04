"""Logstash integration: endpoint resolution, formatting, transport and handler.

The pieces are split by responsibility: :mod:`endpoint` resolves *where* to
send, :mod:`filter` and :mod:`formatter` decide *what* is sent, :mod:`transport`
performs the delivery (including Aion platform authentication), and
:mod:`handler` wires them together.
"""

from .endpoint import LogstashEndpoint, is_platform_host, resolve_logstash_endpoint
from .filter import AionLogstashFilter
from .formatter import AionLogstashFormatter
from .handler import AionLogstashHandler
from .transport import AionLogstashTransport

__all__ = [
    "AionLogstashFilter",
    "AionLogstashFormatter",
    "AionLogstashHandler",
    "AionLogstashTransport",
    "LogstashEndpoint",
    "is_platform_host",
    "resolve_logstash_endpoint",
]
