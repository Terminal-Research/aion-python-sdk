"""Tests for the shared ``aion.adk`` namespace package."""

from __future__ import annotations

from itertools import permutations
import os
import subprocess
import sys
from pathlib import Path


def test_adk_namespace_submodules_survive_path_order() -> None:
    """Verify each ADK package contributes submodules to ``aion.adk``."""
    libs = Path(__file__).resolve().parents[2]
    adk_src = libs / "aion-adk" / "src"
    authoring_adk_src = libs / "aion-authoring-adk" / "src"
    plugin_adk_src = libs / "aion-plugin-adk" / "src"
    api_client_src = libs / "aion-api-client" / "src"
    core_src = libs / "aion-core" / "src"
    script = """
from importlib import import_module
from importlib.util import find_spec

import aion.adk as adk

models = import_module("aion.adk.models")
assert callable(models.aion_lite_llm)
assert find_spec("aion.adk.plugin") is not None
assert find_spec("aion.adk.mcp") is not None
assert not hasattr(adk, "__all__")
"""

    for ordered_sources in permutations((adk_src, authoring_adk_src, plugin_adk_src)):
        env = os.environ.copy()
        paths = [
            *(str(source) for source in ordered_sources),
            str(api_client_src),
            str(core_src),
            env.get("PYTHONPATH", ""),
        ]
        env["PYTHONPATH"] = os.pathsep.join(path for path in paths if path)
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        )
