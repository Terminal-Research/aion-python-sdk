"""``aion serve`` without platform credentials: local mode, and a warning that says so."""

from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest
from asyncclick.testing import CliRunner

from aion.cli.cli import cli
from aion.cli.commands.serve import LOCAL_MODE_WARNING


@pytest.mark.parametrize(("has_credentials", "warned"), [(False, True), (True, False)], ids=["local", "platform"])
async def test_serve_warns_when_authentication_is_disabled(monkeypatch, caplog, has_credentials, warned) -> None:
    monkeypatch.setattr("aion.server.auth.mode.api_settings", Mock(has_credentials=has_credentials))
    monkeypatch.setattr("aion.core.logging.set_process_role", Mock())
    monkeypatch.setattr("aion.server.logging.setup_root_logger", Mock())
    # Stop right after the check: nothing is configured to serve.
    monkeypatch.setattr(
        "aion.core.config.reader.AionConfigReader.load_and_validate_config", Mock(side_effect=RuntimeError("stop"))
    )

    with caplog.at_level(logging.WARNING):
        await CliRunner().invoke(cli, ["serve"])

    assert (LOCAL_MODE_WARNING in caplog.text) is warned
