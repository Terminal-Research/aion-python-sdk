"""Codex provider selection: which credentials and quota pay for a run.

Deployment-operator territory, env-only — never a directive field. Kept in its
own module (no toolkit imports) so `handler.availability()` can pre-flight the
configuration without pulling in the optional toolkit distribution.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .errors import ExtensionSetupError

if TYPE_CHECKING:
    from .settings import EvolutionSettings

__all__ = ["PROVIDERS", "AION", "LOCAL_SESSION", "CUSTOM", "resolve_provider", "warn_ignored_keys"]

logger = logging.getLogger(__name__)

PROVIDER_ENV_VAR = "CODEX_PROVIDER"

AION = "aion"
LOCAL_SESSION = "local_session"
CUSTOM = "custom"

PROVIDERS = (AION, LOCAL_SESSION, CUSTOM)

# Env vars each provider actually reads, as (variable, settings field). Anything
# set outside its provider's entry is ignored with a warning rather than an
# error: a leftover variable is harmless, but silently paying with the wrong
# quota is not — so we say so.
_RELEVANT_KEYS = {
    AION: (),
    LOCAL_SESSION: (("CODEX_HOME", "codex_home"),),
    CUSTOM: (("CODEX_BASE_URL", "codex_base_url"), ("CODEX_API_KEY", "codex_api_key")),
}

_ALL_PROVIDER_KEYS = tuple(sorted({key for keys in _RELEVANT_KEYS.values() for key in keys}))


def resolve_provider(settings: "EvolutionSettings" = None) -> str:
    """The configured Codex provider, normalized.

    Args:
        settings: An already-read environment. One is read here when omitted,
            which is what `handler.availability()` does - it pre-flights the
            configuration before any request has arrived to read it for.

    Raises:
        ExtensionSetupError: unset or not one of `PROVIDERS`. There is no
            default on purpose - falling back to `aion` would mean a
            misconfigured deployment silently spends the platform's model
            quota.
    """
    if settings is None:
        from .settings import EvolutionSettings

        settings = EvolutionSettings()

    raw = settings.provider
    if not raw:
        raise ExtensionSetupError(
            f"{PROVIDER_ENV_VAR} is not set - it must be one of "
            f"{', '.join(PROVIDERS)} (it decides whose credentials and quota "
            "pay for the model calls, so this deployment will not guess)"
        )
    if raw not in PROVIDERS:
        raise ExtensionSetupError(
            f"{PROVIDER_ENV_VAR}={raw!r} is not a known provider - "
            f"expected one of {', '.join(PROVIDERS)}"
        )
    return raw


def warn_ignored_keys(provider: str, settings: "EvolutionSettings" = None) -> None:
    """Log every provider env var that is set but unused under `provider`.

    Args:
        provider: The resolved provider.
        settings: An already-read environment; one is read here when omitted.
    """
    if settings is None:
        from .settings import EvolutionSettings

        settings = EvolutionSettings()

    relevant = dict(_RELEVANT_KEYS[provider])
    for key, attribute in _ALL_PROVIDER_KEYS:
        if key not in relevant and getattr(settings, attribute):
            logger.warning(
                "%s is set but ignored: %s=%s does not read it",
                key,
                PROVIDER_ENV_VAR,
                provider,
            )
