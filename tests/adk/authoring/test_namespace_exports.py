"""Tests for the ``aion.adk.authoring`` namespace package."""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec


def test_adk_authoring_submodules_are_importable() -> None:
    """Verify the ADK authoring package exposes its submodules."""
    models = import_module("aion.adk.authoring.models")
    assert callable(models.aion_lite_llm)
    assert find_spec("aion.adk.authoring.mcp") is not None
