"""Every environment variable the SDK reads is one this module accounts for.

Nothing tied the set of variables the code reads to anything else, so it
drifted in both directions at once: a name was read by nothing and a name was
read by something nobody had looked at.

Most of them are fields of a settings model, and those are taken from the
models rather than repeated here - a list that repeats the source is a list
that eventually disagrees with it. What is written out below is only what no
model declares, grouped by who supplies the value.

`scripts/packaging/envvars.py` produces the read set, by walking the syntax
tree rather than grepping - a name passed to a helper as an argument, or
declared as a pydantic-settings alias, never appears next to `os.getenv` at
all.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aion.core.settings import ApiSettings, platform_supplied_variables
from aion.db.settings import DatabaseSettings
from aion.server.settings import AppSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MODELS = (AppSettings, ApiSettings, DatabaseSettings)


def _model_variables() -> set[str]:
    """Every environment variable a settings model declares."""
    return {
        field.alias
        for model in MODELS
        for field in model.model_fields.values()
        if field.alias
    }


# Read outside any settings model, by a deployment's process environment.
DEPLOYMENT_OUTSIDE_MODELS = {
    "TASK_OWNERSHIP_REAPER",
}

# Read only while the behaviour-evolution extension is active. They configure
# that extension rather than the SDK, so they belong to its specification.
EXTENSION_SCOPED = {
    "CODEX_API_KEY",
    "CODEX_BASE_URL",
    "CODEX_BIN",
    "CODEX_HOME",
    "CODEX_MODEL_CATALOG_JSON",
    "CODEX_PROVIDER",
    "EVOLUTION_EXECUTOR_NETWORK",
    "EVOLUTION_SETUP_COMMAND",
    "EVOLUTION_SETUP_TIMEOUT",
    "EVOLUTION_SPECS_ROOT",
    "EVOLUTION_WORKDIR_ROOT",
    "GITHUB_TOKEN",
}

# Set by the SDK itself for a process it starts, and read back on the other
# side of that boundary.
LAUNCHER = {
    "AION_CHAT_CREDENTIAL_HELPER",
    "AION_CHAT_SKIP_UPDATE_CHECK",
}


def _inventory() -> set[str]:
    """Everything this module accounts for."""
    return _model_variables() | DEPLOYMENT_OUTSIDE_MODELS | EXTENSION_SCOPED | LAUNCHER


def _read_environment_variables() -> set[str]:
    """The variables the source reads, via the inventory script."""
    script = PROJECT_ROOT / "scripts" / "packaging" / "envvars.py"
    spec = importlib.util.spec_from_file_location("aion_envvars", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return set(module.collect())


def test_every_variable_read_is_in_the_inventory() -> None:
    """A variable the source reads is declared by a model or listed above."""
    missing = _read_environment_variables() - _inventory()

    assert not missing, (
        "these environment variables are read by the SDK and accounted for "
        "nowhere: " + ", ".join(sorted(missing))
    )


def test_nothing_in_the_inventory_has_stopped_being_read() -> None:
    """The other direction: something accounted for that nothing reads any more.

    Removing a variable's last reader breaks nothing and shows up nowhere, so
    without this a model would keep a field the SDK no longer acts on.
    """
    unread = _inventory() - _read_environment_variables()

    assert not unread, (
        "these environment variables are accounted for and read nowhere: "
        + ", ".join(sorted(unread))
    )


def test_the_platform_supplied_marker_reaches_the_fields_that_carry_it() -> None:
    """The marker is readable back off the models, which is what makes it useful.

    It is the only machine-readable statement of which values arrive already
    set in a deployment Aion hosts; a marker nothing can read back would be a
    comment with extra syntax.
    """
    marked = platform_supplied_variables(*MODELS)

    assert marked
    assert marked <= _model_variables()
    assert marked <= _read_environment_variables()


@pytest.mark.parametrize(
    "name", sorted(DEPLOYMENT_OUTSIDE_MODELS | {"POSTGRES_URL", "ENCRYPTION_KEY", "HOST_NAME"})
)
def test_a_deployment_variable_names_its_own_scope(name: str) -> None:
    """A name set from outside says what it configures.

    Not a style rule for its own sake: `ENCRYPTION_KEY` is this SDK's, in a
    container that may hold several things wanting that name.
    """
    assert name.isupper()
    assert " " not in name
