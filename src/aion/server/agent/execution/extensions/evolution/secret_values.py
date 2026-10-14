"""The deployment's secret values, for the toolkit to keep out of a run's output.

The executor can read files on its host - its sandbox confines writes, not
reads - so a value from the server's own environment, loaded there from
`.env`, can reach what it prints or writes. The toolkit replaces every value
it knows in the events and the result a run yields; this module tells it
which values the deployment holds.

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
        environ: The environment to read; the process environment by default.

    Returns:
        The values, unfiltered by length - the toolkit ignores values too
        short to be worth replacing.
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
