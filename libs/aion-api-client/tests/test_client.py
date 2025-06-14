"""Tests for the Aion API client."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
import logging

import pytest

from aion.api_client import AionApiClient, settings


def test_settings_loaded() -> None:
    assert settings.debug is True
    assert settings.aion_api.keepalive == 60


def test_missing_env_logs_error(monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR)
    monkeypatch.delenv("AION_CLIENT_ID", raising=False)
    monkeypatch.delenv("AION_SECRET", raising=False)
    AionApiClient()
    assert any(
        "AION_CLIENT_ID and AION_SECRET" in message for message in caplog.messages
    )

