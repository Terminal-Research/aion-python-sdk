"""The deployment's secret values, and their removal from what a toolkit yields.

A toolkit is trusted code, but what it reports comes from work it does on
this host: a process it runs can read files here, and a value from the
server's own environment can reach its output. The server therefore masks
every secret value it knows in everything a toolkit yields before it reaches a
task or a client, and hands the same values to the toolkit
(``ToolkitContext.secret_values``) so it can mask what it writes elsewhere.

A variable counts when its name says it holds a credential. A URL counts for
the password it embeds: a database URL is named for what it points at, not
for the secret inside it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

__all__ = ["MASK", "MIN_SECRET_LENGTH", "SecretMasker", "deployment_secret_values"]

MASK = "[REDACTED]"
"""What a secret value is replaced with."""

MIN_SECRET_LENGTH = 8
"""Values shorter than this are not masked.

Replacing every ``true`` or port number a misnamed variable holds would
mangle the output and protect nothing.
"""

_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSW|KEY|CREDENTIAL|PRIVATE|AUTH", re.IGNORECASE)


def deployment_secret_values(environ: Optional[Mapping[str, str]] = None) -> list[str]:
    """Every value in the environment that is, or embeds, a secret.

    Args:
        environ: The variables to read; the process environment by default.

    Returns:
        The values, unfiltered by length - ``SecretMasker`` ignores values
        too short to be worth replacing.
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


class SecretMasker:
    """Replaces known secret values in strings and JSON values.

    Values can be added while a turn runs: a short-lived token the server
    mints for the toolkit is masked from the moment it exists. The values are
    never part of ``repr()``.
    """

    def __init__(self, values: Iterable[str] = ()) -> None:
        """Start with ``values``; those shorter than ``MIN_SECRET_LENGTH`` are ignored."""
        self._values: dict[str, None] = {}
        for value in values:
            self.add(value)

    def add(self, value: Optional[str]) -> None:
        """Mask one more value from now on."""
        if value and len(value) >= MIN_SECRET_LENGTH:
            self._values[value] = None

    @property
    def values(self) -> tuple[str, ...]:
        """The values being masked."""
        return tuple(self._values)

    def __repr__(self) -> str:
        return f"SecretMasker({len(self._values)} values)"

    def text(self, value: str) -> str:
        """``value`` with every known secret replaced by ``MASK``."""
        # Longest first, so a value that contains another is replaced whole.
        for secret in sorted(self._values, key=len, reverse=True):
            if secret in value:
                value = value.replace(secret, MASK)
        return value

    def json(self, value: Any) -> Any:
        """A copy of a JSON value with every string in it masked; keys are kept."""
        if not self._values:
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {key: self.json(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.json(item) for item in value]
        return value
