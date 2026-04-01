"""Pytest configuration for the ``aion-cli`` test suite."""

from __future__ import annotations

import logging
import os
import sys
import types


SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


def _install_test_stubs() -> None:
    """Install lightweight module stubs for dependencies outside ``aion-cli``.

    The CLI package imports serve/chat commands at module import time, which in turn
    depend on sibling packages that are not installed in this isolated test
    environment. These stubs keep the tests focused on ``aion-cli`` behavior.
    """

    import click

    sys.modules.setdefault("asyncclick", click)

    handlers_module = types.ModuleType("aion.cli.handlers")

    async def start_chat(**_kwargs):
        return None

    class ServeHandler:
        """Minimal stand-in for the real serve handler."""

        async def run(self, **_kwargs) -> None:
            return None

    handlers_module.start_chat = start_chat
    handlers_module.ChatSession = object
    handlers_module.ServeHandler = ServeHandler
    sys.modules.setdefault("aion.cli.handlers", handlers_module)

    reader_module = types.ModuleType("aion.shared.config.reader")

    class ConfigurationError(Exception):
        """Test double for the shared configuration exception."""

    class AionConfigReader:
        """Minimal config reader that returns an empty agent list."""

        def load_and_validate_config(self):
            return types.SimpleNamespace(agents=[])

    reader_module.ConfigurationError = ConfigurationError
    reader_module.AionConfigReader = AionConfigReader
    sys.modules.setdefault("aion.shared.config.reader", reader_module)

    logging_module = types.ModuleType("aion.shared.logging")
    logging_module.get_logger = lambda: logging.getLogger("aion-cli-tests")
    sys.modules.setdefault("aion.shared.logging", logging_module)


_install_test_stubs()
