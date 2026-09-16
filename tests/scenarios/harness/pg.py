"""PostgreSQL for the persistence variant.

The scenarios never start a database. ``POSTGRES_TEST_URL`` names one that
may be wiped - the Makefile supplies it the same way the SDK's own
integration tests do - and the persistence variant passes it to the server as
``POSTGRES_URL``, which is what turns the in-memory task store into a durable
one.
"""

from __future__ import annotations

import os
from typing import Optional

__all__ = ["postgres_url", "postgres_env", "have_postgres"]

POSTGRES_TEST_URL_VAR = "POSTGRES_TEST_URL"


def postgres_url() -> Optional[str]:
    """The database the persistence scenarios may use, or None."""
    return os.environ.get(POSTGRES_TEST_URL_VAR) or None


def have_postgres() -> bool:
    """Whether a database was named for this run."""
    return postgres_url() is not None


def postgres_env() -> dict[str, str]:
    """Environment that puts the server on that database."""
    url = postgres_url()
    if url is None:
        raise RuntimeError(
            f"{POSTGRES_TEST_URL_VAR} is not set; run the persistence scenarios via `make tests-scenarios-pg`."
        )
    return {"POSTGRES_URL": url}
