from __future__ import annotations

import logging

__all__ = ["setup_root_logger"]


def setup_root_logger():
    """Configure the root logger with Aion stream and Logstash handlers."""
    from aion.server.settings import app_settings
    from aion.core.settings import api_settings
    from .filters import BASE_RULES, NamespaceFilter, ServerAionContextFilter
    from .handlers import AionLogstashHandler, LogStreamHandler

    root = logging.getLogger()

    if any(isinstance(h, LogStreamHandler) for h in root.handlers):
        return

    root.setLevel(app_settings.log_level)

    log_namespace_filter = NamespaceFilter(BASE_RULES)

    stream_handler = LogStreamHandler()
    stream_handler.addFilter(ServerAionContextFilter())
    stream_handler.addFilter(log_namespace_filter)
    root.addHandler(stream_handler)

    logstash_handler = AionLogstashHandler(
        host=app_settings.logstash_host,
        port=app_settings.logstash_port,
        database_path=None,
        transport="logstash_async.transport.HttpTransport",
        ssl_enable=False,
        ssl_verify=False,
        enable=app_settings.is_logstash_configured,
        client_id=api_settings.client_id,
        node_name=app_settings.node_name,
    )
    logstash_handler.addFilter(log_namespace_filter)
    root.addHandler(logstash_handler)
