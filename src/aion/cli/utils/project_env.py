"""The project's ``.env``: read by the ``aion`` CLI and by nothing else in the SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

__all__ = ["DOTENV_DISABLED_ENV_VAR", "load_project_env"]

DOTENV_FILE = ".env"

DOTENV_DISABLED_ENV_VAR = "PYTHON_DOTENV_DISABLED"
"""python-dotenv's own switch: set to a true value, ``load_dotenv()`` reads nothing."""


def load_project_env(directory: Optional[Path] = None) -> Optional[Path]:
    """Load the project's ``.env`` into the process environment.

    An Aion project runs through ``aion`` from its own directory, so the CLI
    is the one place its ``.env`` is read: from the directory the command runs
    in, before any command reads a setting. The SDK's settings read the
    process environment and nothing else, and the agent processes
    ``aion serve`` starts inherit what is loaded here. A variable already set
    in the environment keeps its value, so a deployment's own configuration is
    never overridden by a file left beside it.

    ``PYTHON_DOTENV_DISABLED`` set to a true value skips the file, as it does
    for every ``load_dotenv()`` in the process.

    Args:
        directory: Where to look for ``.env``. Defaults to the current
            directory.

    Returns:
        The file loaded, or None when there is none or loading is disabled.
    """
    path = (directory or Path.cwd()) / DOTENV_FILE
    if not path.is_file():
        return None
    return path if load_dotenv(path, override=False) else None
