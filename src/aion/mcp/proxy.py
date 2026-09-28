"""Load an ASGI proxy for the configured MCP server."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from aion.core.config.exceptions import ConfigurationError
from aion.core.config.reader import AionConfigReader

# The distribution is asgi-proxy-lib; the module it installs is asgi_proxy,
# and asgi_proxy() is a factory returning an ASGI application, not a class.
# Unguarded: this module is imported only from aion.mcp.load_proxy(), whose
# wrapper turns the missing library into a named extra.
from asgi_proxy import asgi_proxy

logger = logging.getLogger(__name__)


def load_proxy(config_path: str | Path = "aion.yaml") -> Any | None:
    """Return an ASGI proxy for the MCP server if configured.

    Reads `aion.yaml` through `AionConfigReader`, the same reader `aion serve`
    uses, so `aion.mcp` is one shape defined in one place. Parsing the file a
    second time here is what let `aion.mcp.port` be a working key that no model
    declared and no page documented, while `aion.http` was a documented key
    nothing read.

    Args:
        config_path: Path to the configuration file, absolute or relative to
            the working directory.

    Returns:
        An ASGI application proxying to the configured MCP port, or None when
        there is no file or it configures no port.

    Raises:
        ConfigurationError: If the file exists but is not a valid `aion.yaml`.
            An unreadable configuration is a startup failure, not an absent
            proxy - reporting it as the latter would mount nothing and say
            nothing.
    """
    path = Path(config_path)
    if not path.is_absolute():
        path = Path(os.getcwd()) / path

    if not path.exists():
        logger.debug("Configuration file %s not found", path)
        return None

    config = AionConfigReader(config_path=path, logger_=logger).load_and_validate_config()

    port = config.mcp.port if config.mcp else None
    if not port:
        logger.debug("No MCP port configured in %s", path)
        return None

    logger.info("Creating MCP proxy for port %s", port)
    return asgi_proxy(f"http://localhost:{port}")
