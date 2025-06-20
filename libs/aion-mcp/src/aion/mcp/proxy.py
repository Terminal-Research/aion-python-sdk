"""Load an ASGI proxy for the configured MCP server."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from asgi_proxy_lib import ASGIProxy
import yaml

logger = logging.getLogger(__name__)




def load_proxy(config_path: str | Path = "aion.yaml") -> Any | None:
    """Return an ASGI proxy for the MCP server if configured."""
    path = Path(config_path)
    if not path.is_absolute():
        path = Path(os.getcwd()) / path

    if not path.exists():
        logger.debug("Configuration file %s not found", path)
        return None

    with path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    port = (
        config.get("aion", {})
        .get("mcp", {})
        .get("port")
    )
    if not port:
        logger.debug("No MCP port configured in %s", path)
        return None

    logger.info("Creating MCP proxy for port %s", port)
    return ASGIProxy(f"http://localhost:{port}")
