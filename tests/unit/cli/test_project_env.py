"""The project's ``.env`` is the CLI's to load, and only when it may."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from asyncclick.testing import CliRunner

from aion.cli.cli import cli
from aion.cli.utils.project_env import DOTENV_DISABLED_ENV_VAR, load_project_env

FROM_FILE = "AION_PROJECT_ENV_PROBE"
ALREADY_SET = "AION_PROJECT_ENV_KEPT"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A project directory with a `.env`, and loading switched back on.

    The suite switches loading off for every test; these are the ones that
    exercise it. What the file sets is removed again afterwards, since
    python-dotenv writes to the environment behind monkeypatch's back.
    """
    (tmp_path / ".env").write_text(
        f"{FROM_FILE}=from-file\n{ALREADY_SET}=from-file\n", encoding="utf-8"
    )
    monkeypatch.delenv(DOTENV_DISABLED_ENV_VAR, raising=False)
    monkeypatch.setenv(ALREADY_SET, "from-environment")
    yield tmp_path
    os.environ.pop(FROM_FILE, None)


def test_the_file_of_the_directory_is_loaded(project: Path) -> None:
    assert load_project_env(project) == project / ".env"
    assert os.environ[FROM_FILE] == "from-file"


def test_a_variable_already_set_keeps_its_value(project: Path) -> None:
    """A deployment's own configuration wins over a file left beside it."""
    load_project_env(project)
    assert os.environ[ALREADY_SET] == "from-environment"


def test_without_a_file_nothing_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DOTENV_DISABLED_ENV_VAR, raising=False)
    assert load_project_env(tmp_path) is None


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_the_switch_turns_loading_off(
    project: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """python-dotenv's switch, honoured whichever python-dotenv is installed."""
    monkeypatch.setenv(DOTENV_DISABLED_ENV_VAR, value)

    assert load_project_env(project) is None
    assert FROM_FILE not in os.environ


async def test_aion_loads_it_from_the_directory_it_runs_in(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the command runs, so every setting a command reads can see it."""
    monkeypatch.chdir(project)

    result = await CliRunner().invoke(cli, ["logs", "--help"])

    assert result.exit_code == 0, result.output
    assert os.environ[FROM_FILE] == "from-file"
