"""
Shared paths and package discovery for the dependency scripts.

Packages are discovered from the filesystem rather than listed by hand: any
directory under `libs/` holding a `pyproject.toml` is a Poetry package. A
hand-maintained list duplicates what `pyproject.toml` already declares and
drifts from it silently, so there is nothing here to keep in sync.
"""

from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).parent.parent.parent
LIBS_DIR = ROOT_DIR / "libs"


def find_pyproject_files() -> List[Path]:
    """
    Find every `pyproject.toml` under `libs/`.

    Returns:
        Sorted list of paths, one per Poetry package
    """
    return sorted(LIBS_DIR.glob("*/pyproject.toml"))


def get_all_packages() -> List[str]:
    """
    Discover all Poetry packages in the monorepo.

    Directories without a `pyproject.toml` (for example the npm-based
    `aion-chat-ui`) are not Poetry packages and are skipped.

    Returns:
        Sorted list of package names
    """
    return [path.parent.name for path in find_pyproject_files()]
