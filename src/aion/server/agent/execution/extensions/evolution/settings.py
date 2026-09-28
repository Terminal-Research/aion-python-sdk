"""The deployment environment the behaviour-evolution extension reads.

Twelve variables decide where an evolution run gets its model, its
credentials and its working directories, and until this module they were read
one `os.environ.get` at a time across five functions of `tools_factory`. Two
of them were checked twice in different places with the same message, and
three were invisible to any survey of what this SDK reads, because their names
travelled into a helper as arguments and never appeared beside `os` at all.

This gathers the reading and the parsing. It deliberately does **not** gather
the *deciding*: no field is required, and nothing here raises for a value that
is merely absent. Which variables a run needs depends on the provider it
selected and on the directive it was given, so those checks stay at the points
that know - `resolve_provider`, `build_worker` - and read typed values from
here instead of strings from the environment.

Not a module-level singleton, unlike `AppSettings`. It is constructed when an
evolution request arrives, so a deployment that never activates this extension
never parses its configuration and never fails on it.

Credentials are the exception, and they are not fields at all - they are
properties reading the environment each time they are asked. Two reasons, and
either would be enough.

The deployment may replace a credential while the pod runs, and an evolution
run clones, commits and pushes over tens of minutes: a value captured when the
run started can be one the forge has already stopped accepting. And a field
holding a secret is a secret that appears in `repr()` and `model_dump()`, so
any log line or traceback carrying this object would carry the token with it.
A property is in neither.

The Codex secret for `CODEX_PROVIDER=aion` is not here at all: it is a
short-lived JWT minted per model call by `credentials_provider`.

Nothing else here is read again, and that is a decision rather than an
omission. The rest are decisions about a run - where its checkout goes,
whether its sandbox has network, what command sets it up - and a run has to be
configured one way for its whole length. Re-reading the network flag halfway
would not change a sandbox that already exists; it would only make this code
disagree with it. They are already dynamic at the granularity that means
something: this object is constructed per request, so every run reads whatever
the environment says when it starts.
"""

from __future__ import annotations

import logging
import os
import shlex
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ExtensionSetupError

__all__ = ["EvolutionSettings"]

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")

GITHUB_TOKEN_ENV_VAR = "GITHUB_TOKEN"
CODEX_API_KEY_ENV_VAR = "CODEX_API_KEY"
"""Named once each, so a field and its re-read cannot name different things."""


def _current(variable: str) -> Optional[str]:
    """Read a variable now, treating blank as absent.

    Args:
        variable: The environment variable to read.

    Returns:
        Its current value, or None when unset or blank.
    """
    return (os.environ.get(variable) or "").strip() or None


class EvolutionSettings(BaseSettings):
    """One reading of the evolution extension's deployment environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Optional[str] = Field(
        default=None,
        alias="CODEX_PROVIDER",
        description=(
            "Whose credentials and quota pay for the model calls. Required, "
            "and checked by resolve_provider() rather than here: there is no "
            "default on purpose, and the message that says so belongs with "
            "the code that knows the alternatives."
        ),
    )

    codex_base_url: Optional[str] = Field(
        default=None,
        alias="CODEX_BASE_URL",
        description="Model endpoint for CODEX_PROVIDER=custom.",
    )

    codex_home: Optional[str] = Field(
        default=None,
        alias="CODEX_HOME",
        description="Codex home directory for CODEX_PROVIDER=local_session.",
    )

    codex_model_catalog_json: Optional[str] = Field(
        default=None,
        alias="CODEX_MODEL_CATALOG_JSON",
        description="Model catalog handed to Codex, as JSON.",
    )

    codex_bin: str = Field(
        default="codex",
        alias="CODEX_BIN",
        description="Codex executable to run.",
    )

    specs_root: Optional[str] = Field(
        default=None,
        alias="EVOLUTION_SPECS_ROOT",
        description=(
            "Where evolution specifications are written. Falls back to the "
            "request's own daemon configuration when unset."
        ),
    )

    workdir_root: Optional[str] = Field(
        default=None,
        alias="EVOLUTION_WORKDIR_ROOT",
        description="Where a run's working checkout is created.",
    )

    executor_network: bool = Field(
        default=False,
        alias="EVOLUTION_EXECUTOR_NETWORK",
        description="Whether the executor sandbox is granted network access.",
    )

    setup_command: Optional[list[str]] = Field(
        default=None,
        alias="EVOLUTION_SETUP_COMMAND",
        description=(
            "Command run in the checkout before the executor starts, written "
            "as a shell-quoted line and split with shlex."
        ),
    )

    setup_timeout: Optional[float] = Field(
        default=None,
        alias="EVOLUTION_SETUP_TIMEOUT",
        description="Seconds the setup command may take.",
    )

    @field_validator("executor_network", mode="before")
    @classmethod
    def _parse_flag(cls, value: object) -> bool:
        """Accept the documented spellings; treat anything else as off, loudly.

        A typo must not silently disable what an operator meant to enable, and
        must not fail the run either - a misspelled flag is not a reason to
        refuse work that would otherwise proceed without network access.
        """
        if isinstance(value, bool):
            return value
        raw = (str(value) if value is not None else "").strip().lower()
        if raw in _TRUTHY:
            return True
        if raw and raw not in _FALSY:
            logger.warning(
                "EVOLUTION_EXECUTOR_NETWORK=%r is not a recognized boolean value "
                "(expected one of %s) - treating as false",
                value,
                ", ".join(_TRUTHY),
            )
        return False

    @field_validator("setup_command", mode="before")
    @classmethod
    def _split_argv(cls, value: object) -> Optional[list[str]]:
        """Split a shell-quoted line, naming the variable when it cannot be.

        Args:
            value: The raw value, or a list when constructed directly.

        Returns:
            argv, or None when unset or blank.

        Raises:
            ExtensionSetupError: The line has unbalanced quotes.
        """
        if value is None or isinstance(value, list):
            return value
        raw = str(value)
        if not raw.strip():
            return None
        try:
            return shlex.split(raw)
        except ValueError as error:
            raise ExtensionSetupError(
                f"EVOLUTION_SETUP_COMMAND is not a valid command line: {error}"
            ) from error

    @field_validator("setup_timeout", mode="before")
    @classmethod
    def _parse_seconds(cls, value: object) -> Optional[float]:
        """Read a number of seconds, naming the variable when it is not one.

        Args:
            value: The raw value.

        Returns:
            The number of seconds, or None when unset or blank.

        Raises:
            ExtensionSetupError: The value is not a number.
        """
        if value is None or isinstance(value, (int, float)):
            return value
        raw = str(value)
        if not raw.strip():
            return None
        try:
            return float(raw)
        except ValueError as error:
            raise ExtensionSetupError(
                f"EVOLUTION_SETUP_TIMEOUT must be a number of seconds, got {raw!r}"
            ) from error

    @property
    def github_token(self) -> Optional[str]:
        """Token used to push the evolution branch to the forge, read now.

        Returns:
            The current token, or None when the variable is unset or blank.
        """
        return _current(GITHUB_TOKEN_ENV_VAR)

    @property
    def codex_api_key(self) -> Optional[str]:
        """API key for `CODEX_PROVIDER=custom`, read now.

        Returns:
            The current key, or None when the variable is unset or blank.
            Absence is a supported configuration: an unauthenticated endpoint,
            such as a local Ollama server.
        """
        return _current(CODEX_API_KEY_ENV_VAR)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> Optional[str]:
        """Lower-case and strip the provider name, leaving the check to its caller."""
        if value is None:
            return None
        raw = str(value).strip().lower()
        return raw or None
