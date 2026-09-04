"""Tests for the shared ``aion.adk.authoring`` namespace package."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_adk_namespace_submodules_survive_path_order() -> None:
    """Verify each ADK package contributes submodules to ``aion.adk.authoring``."""
    libs = Path(__file__).resolve().parents[2]
    authoring_adk_src = libs / "aion-authoring-adk" / "src"
    api_client_src = libs / "aion-api-client" / "src"
    core_src = libs / "aion-core" / "src"
    script = """
from importlib import import_module
from importlib.util import find_spec

models = import_module("aion.adk.authoring.models")
assert callable(models.aion_lite_llm)
assert find_spec("aion.adk.authoring.mcp") is not None
"""

    env = os.environ.copy()
    paths = [
        str(authoring_adk_src),
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
