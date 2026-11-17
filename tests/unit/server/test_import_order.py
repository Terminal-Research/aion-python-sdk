"""Every module directly under ``aion.server`` imports first, in a fresh interpreter.

The module a process imports first decides whether an import cycle between
the server's packages shows: an order that resolves a cycle hides it, and
another order breaks on it - a test worker that collects a test of
``aion.server.tasks`` before anything else, or an application that imports
that package directly.
"""

import pkgutil
import subprocess
import sys

import pytest

import aion.server

MODULES = sorted(f"aion.server.{module.name}" for module in pkgutil.iter_modules(aion.server.__path__))


@pytest.mark.parametrize("module", MODULES)
def test_a_module_imports_before_any_other(module: str) -> None:
    result = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
