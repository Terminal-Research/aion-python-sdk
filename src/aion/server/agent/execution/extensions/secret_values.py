"""The deployment's secret values, for an executor that masks them in what it reports.

An executable extension is trusted code, but what it reports comes from work
it does on this host: a process it runs can read files here, and a value from
the server's own environment can reach its output. An executor masks the
deployment's secret values in everything it yields and writes elsewhere (a
commit, a pull request); ``deployment_secret_values`` is how it learns them.

A variable counts when its name says it holds a credential. A URL counts for
the password it embeds: a database URL is named for what it points at, not
for the secret inside it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Optional
from urllib.parse import unquote, urlsplit

__all__ = ["deployment_secret_values"]

_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSW|KEY|CREDENTIAL|PRIVATE|AUTH", re.IGNORECASE)


def deployment_secret_values(environ: Optional[Mapping[str, str]] = None) -> list[str]:
    """Every value in the environment that is, or embeds, a secret.

    Args:
        environ: The variables to read; the process environment by default.

    Returns:
        The values, unfiltered by length: a masker ignores values too short
        to be worth replacing.
    """
    environ = os.environ if environ is None else environ
    values: list[str] = []
    for name, value in environ.items():
        if not value:
            continue
        if _SECRET_NAME.search(name):
            values.append(value)
        if "://" in value:
            password = _url_password(value)
            if password:
                values += [password, unquote(password)]
    return values


def _url_password(value: str) -> Optional[str]:
    """The password embedded in a URL, as written, or None."""
    try:
        return urlsplit(value).password
    except ValueError:
        return None
