"""scripts/packaging/envcheck.py, the check CI runs on the installed environment.

The interesting case cannot be staged in the real environment - the whole point
of the script is that the real environment must never contain it - so the
duplicate is planted in a directory of made-up ``*.dist-info`` entries and the
script is pointed at that instead.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "packaging" / "envcheck.py"


def _load_envcheck():
    """Import the script by path: scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("envcheck", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


envcheck = _load_envcheck()


def plant(directory: Path, *distributions: str) -> Path:
    """Create empty ``<name>-<version>.dist-info`` directories."""
    for distribution in distributions:
        (directory / f"{distribution}.dist-info").mkdir(parents=True)
    return directory


def test_one_version_each_is_not_a_duplicate(tmp_path: Path) -> None:
    plant(tmp_path, "mcp-1.29.0", "openai-2.54.0", "opentelemetry_api-1.41.1")

    assert envcheck.duplicates([tmp_path]) == {}


def test_two_versions_of_one_package_are_found(tmp_path: Path) -> None:
    plant(tmp_path, "mcp-1.29.0", "mcp-2.1.1", "openai-2.54.0")

    found = envcheck.duplicates([tmp_path])

    assert set(found) == {"mcp"}
    assert sorted(version for version, _ in found["mcp"]) == ["1.29.0", "2.1.1"]


def test_names_are_compared_after_pep_503_normalisation(tmp_path: Path) -> None:
    """``opentelemetry.api`` and ``opentelemetry_api`` are the same project."""
    plant(tmp_path, "opentelemetry_api-1.41.1", "opentelemetry.api-1.44.0")

    assert set(envcheck.duplicates([tmp_path])) == {"opentelemetry-api"}


def test_the_script_fails_on_a_directory_holding_a_duplicate(tmp_path: Path) -> None:
    plant(tmp_path, "mcp-1.29.0", "mcp-2.1.1")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--site-packages", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "mcp is installed 2 times: 1.29.0, 2.1.1" in result.stdout
    assert "1 check(s) failed" in result.stdout


def test_the_script_passes_on_a_clean_directory(tmp_path: Path) -> None:
    plant(tmp_path, "mcp-1.29.0", "openai-2.54.0")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--site-packages", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed" in result.stdout


@pytest.mark.parametrize("name", ["mcp", "opentelemetry-api"])
def test_the_real_environment_has_one_version_of_each(name: str) -> None:
    """The regression this script exists for, asserted where the suite runs."""
    installed = envcheck.installed_versions(envcheck.default_site_packages())

    assert len(installed.get(name, [])) <= 1, installed.get(name)
