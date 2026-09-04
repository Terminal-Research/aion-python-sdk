"""Fixtures shared across the whole suite."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest


# Installed in the child interpreter through sitecustomize, before anything
# else imports: a meta path finder that refuses the named modules the way an
# absent installation would.
_BLOCKER = '''\
import sys
from importlib.abc import MetaPathFinder

BLOCKED = {blocked!r}


class _Blocker(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top in BLOCKED or any(name == b or name.startswith(b + ".") for b in BLOCKED):
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None


sys.meta_path.insert(0, _Blocker())
'''


@pytest.fixture
def run_python_without(
    tmp_path: Path,
) -> Callable[[Sequence[str], str], subprocess.CompletedProcess]:
    """Run Python code in an interpreter where some modules cannot be imported.

    The one honest way to test what a thinner install does. Extras only add
    third-party libraries, so an install without one is this environment minus
    a handful of importable names - but the suite has already imported half of
    them by the time any test runs, and unpicking sys.modules in process would
    describe nothing real. A subprocess starts clean.
    """

    def run(blocked: Sequence[str], code: str) -> subprocess.CompletedProcess:
        (tmp_path / "sitecustomize.py").write_text(
            _BLOCKER.format(blocked=set(blocked)), encoding="utf-8"
        )
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
        )

    return run
