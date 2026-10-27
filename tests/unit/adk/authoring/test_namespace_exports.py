"""Tests for the ``aion.adk.authoring`` namespace package."""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec


def test_adk_authoring_submodules_are_importable() -> None:
    """Verify the ADK authoring package exposes its submodules."""
    models = import_module("aion.adk.authoring.models")
    assert callable(models.aion_lite_llm)
    assert find_spec("aion.adk.authoring.mcp") is not None


def test_adk_authoring_exports_the_emit_helpers() -> None:
    """The emit helpers an agent writes against are importable from the package itself."""
    authoring = import_module("aion.adk.authoring")
    for name in ("emit_artifact", "emit_card", "emit_message", "emit_reaction", "emit_thinking"):
        assert callable(getattr(authoring, name))
        assert name in authoring.__all__
