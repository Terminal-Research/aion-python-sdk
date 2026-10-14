"""Tests run on the SDK's defaults, whatever the machine running them holds.

``tests/environment.py`` takes a developer's configuration out of the
environment before collection; these hold it to that, on a mapping and on the
settings this very process built.
"""

from __future__ import annotations

import os

import pytest

from aion.core.settings import ApiSettings
from aion.db.settings import DatabaseSettings
from aion.server.settings import AppSettings
from tests.environment import DOTENV_DISABLED, LITELLM_LOCAL_COST_MAP, isolate


def test_the_machines_configuration_is_taken_out_and_the_rest_left() -> None:
    """Credentials, a database and a storage backend go; the shell itself stays.

    Names are matched without case, the way the SDK's settings read them.
    """
    environ = {
        "AION_CLIENT_ID": "real-client",
        "aion_api_host": "https://api.aion.to",
        "FILE_STORAGE_BACKEND": "aion",
        "POSTGRES_URL": "postgresql://production/aion",
        "OPENAI_API_KEY": "sk-real",
        "PATH": "/usr/bin",
        "POSTGRES_TEST_URL": "postgresql://localhost/test",
    }

    isolate(environ)

    assert environ == {
        "PATH": "/usr/bin",
        "POSTGRES_TEST_URL": "postgresql://localhost/test",
        DOTENV_DISABLED: "1",
        LITELLM_LOCAL_COST_MAP: "True",
    }


@pytest.mark.parametrize("model", [ApiSettings, AppSettings, DatabaseSettings])
def test_the_settings_read_in_this_process_are_the_defaults(model) -> None:
    """What the suite actually runs on, not only what isolate() does to a mapping.

    Read afresh rather than from the SDK's singletons, which `aion serve`
    updates at run time and a test of that may leave updated. A value from
    the machine - its shell or a `.env` beside the tests - would show up as
    a field that is not its default.
    """
    read, defaults = model(), model.model_construct()

    assert {
        name: getattr(read, name)
        for name in model.model_fields
        if getattr(read, name) != getattr(defaults, name)
    } == {}


def test_no_process_a_test_starts_reads_a_dotenv() -> None:
    """A child inherits the switch, so an `aion` started by a test reads no `.env`."""
    assert os.environ[DOTENV_DISABLED] == "1"
