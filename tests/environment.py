"""The environment every test runs in: the SDK's defaults, not the machine's.

A developer's shell and ``.env`` hold real configuration - platform
credentials, a database, a file storage backend. A test that picked any of it
up would test that configuration instead of the code, and could reach the
service it points at with the developer's credentials. So before anything
imports the SDK, every variable the SDK reads is removed from the environment,
along with the credentials of the third-party services the frameworks under
test reach on their own, and ``.env`` loading is switched off for the test
process and every process it starts. LiteLLM is kept on the model cost map it
ships with, rather than fetching one from GitHub when it is imported. A test
that needs a setting sets it itself.

The SDK's variables come from ``scripts/packaging/envvars.py``, the inventory
of everything the source reads, so a setting is covered from the day it is
added.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import MutableMapping
from pathlib import Path

__all__ = [
    "DOTENV_DISABLED",
    "LITELLM_LOCAL_COST_MAP",
    "THIRD_PARTY_VARIABLES",
    "isolate",
    "sdk_variables",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DOTENV_DISABLED = "PYTHON_DOTENV_DISABLED"
"""python-dotenv's switch, which the ``aion`` CLI honours as well."""

LITELLM_LOCAL_COST_MAP = "LITELLM_LOCAL_MODEL_COST_MAP"
"""LiteLLM's switch for the bundled cost map instead of a download on import."""

THIRD_PARTY_VARIABLES = frozenset({
    # Model providers the frameworks call when a test builds a model client.
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    # Tracing that would ship what a test runs to LangSmith.
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGSMITH_TRACING",
})
"""Credentials and switches of services the SDK does not read but its frameworks do."""


def sdk_variables() -> frozenset[str]:
    """Every environment variable the SDK's source reads."""
    script = PROJECT_ROOT / "scripts" / "packaging" / "envvars.py"
    spec = importlib.util.spec_from_file_location("aion_envvars", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return frozenset(module.collect())


def isolate(environ: MutableMapping[str, str] = os.environ) -> None:
    """Take the machine's configuration out of ``environ``; switch downloads off.

    Names are compared without case: the SDK's settings read them that way.
    """
    removed = {name.upper() for name in sdk_variables() | THIRD_PARTY_VARIABLES}
    for name in [name for name in environ if name.upper() in removed]:
        del environ[name]
    environ[DOTENV_DISABLED] = "1"
    environ[LITELLM_LOCAL_COST_MAP] = "True"
