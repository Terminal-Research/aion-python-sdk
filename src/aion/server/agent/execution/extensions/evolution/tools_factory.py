"""Lazy toolkit boundary: request + environment config -> EvolutionWorker.

The only module in the evolution package that imports the optional
aion-toolkit-behaviour-evolution-python distribution - handler.py imports it
lazily inside stream(), so nothing in aion.server needs the toolkit installed
until an evolution request arrives.

Codex credentials follow the toolkit's own DI: ``credentials_provider`` is
optional, and this wrapper decides whether to attach one, based on the
required ``CODEX_PROVIDER``:

  * ``aion`` - the Aion model service (aion.api's
    ``aion_model_base_url()``) with a token resolver that mints a fresh
    short-lived Aion JWT per call plus the agent's daemon-identity principal
    (``Aion-Principal-Selector`` header, from the request's
    ``environment.daemon_agent_identity_id``) - secret and principal travel
    per-call and are never stored.

    NOT YET DEPLOYABLE (2026-08-27): ``aion_model_base_url()`` resolves to
    ``AION_API_HOST`` - the same host used for the platform's GraphQL/WS
    traffic, with no endpoint of its own carved out for Codex model calls -
    and no ``AION_API_HOST`` has been provisioned/agreed for this deployment
    yet. Until that URL exists (and it's decided whether Codex gets a
    dedicated model-service endpoint or shares the platform host as-is),
    ``CODEX_PROVIDER=aion`` has nothing to talk to. Use ``local_session`` or
    ``custom`` until this is resolved.
  * ``local_session`` - the operator's own logged-in Codex CLI session
    (``~/.codex/auth.json``, or ``CODEX_HOME``) - usage counts against their
    personal subscription limits, not this deployment's Aion model service
    quota or any API key.
  * ``custom`` - any OpenAI-Responses-compatible endpoint at ``CODEX_BASE_URL``,
    optionally authenticated with a static ``CODEX_API_KEY`` sent as
    ``Authorization: Bearer`` - no principal, nothing to attribute usage to.

There is no default provider - see `_codex_access`/`resolve_provider`: an
unset or unknown ``CODEX_PROVIDER`` fails the run rather than silently
guessing whose quota pays for it.

Behavior configuration is request-bound: the daemon environment's
configuration variables (aion.yaml config fields, parameterized back by the
control plane) name the Codex model (``llm``) as a fallback default. Env vars
for that exist only as local-dev overrides. Deployment-scoped concerns (git
credentials, binaries, host filesystem paths, sandbox/network grants, build
hooks) stay in the environment - GITHUB_TOKEN in particular never comes from
the request: a token in the directive would leak into Codex prompts and logs.

Everything else about how a run behaves lives in the directive itself:
per-run model tuning (``EvolutionDirectiveEventPayload.model`` - name,
reasoning effort, context window), the branch strategy (``branch_strategy``),
and resource ceilings the caller sets for its own run
(``EvolutionDirectiveEventPayload.limits`` - token budget, timeouts). None of
these are deployment env vars any more, and none of them are clamped against
one: this deployment is called by one trusted control plane, so `limits` is
the caller protecting its own run, not a boundary this deployment enforces
against the caller. What stays env-only is exactly what would leak a secret,
name a host filesystem path, name the executable that gets exec'd, or widen
the sandbox - see `_codex_access` for exactly which fields those are and why.

A malformed field on the directive (e.g. a non-numeric timeout) is rejected
by the payload's own pydantic validation before it ever reaches this module -
there is no env-style manual parsing/ExtensionSetupError step for `model`,
`branch_strategy`, or `limits`.

Env:
    CODEX_PROVIDER                      required, one of aion | local_session |
                                        custom - decides whose credentials and
                                        quota pay for the model calls; no
                                        default, an unset value fails the run.
                                        NOT YET DEPLOYABLE: no AION_API_HOST has
                                        been provisioned for this deployment, so
                                        aion has nothing to talk to yet - see the
                                        module docstring's `aion` bullet
    CODEX_BASE_URL                      required by CODEX_PROVIDER=custom,
                                        ignored otherwise - the OpenAI-Responses
                                        -compatible endpoint to call
    CODEX_API_KEY                       optional under CODEX_PROVIDER=custom,
                                        ignored otherwise - sent as
                                        Authorization: Bearer; omit it for an
                                        unauthenticated endpoint (e.g. ollama)
    CODEX_HOME                          optional under CODEX_PROVIDER=local_session,
                                        ignored otherwise - the real CODEX_HOME
                                        to source auth.json from (default ~/.codex);
                                        usage counts against the operator's
                                        personal subscription limits
    CODEX_MODEL_CATALOG_JSON            optional catalog for local models;
                                        deployment env only, never the request -
                                        it names a path on the host filesystem
    CODEX_BIN                           optional, default "codex"; deployment
                                        env only, never the request - it names
                                        the executable that gets exec'd
    GITHUB_TOKEN                        required, injected JIT into git calls
    EVOLUTION_SPECS_ROOT                optional spec-convention root override;
                                        deployment env only, never the request -
                                        it can name a write path on the host
                                        filesystem outside the cloned workdir
    EVOLUTION_EXECUTOR_NETWORK          optional, "1"/"true" grants the executor
                                        sandbox network access (installs allowed,
                                        briefing demands manifest declaration);
                                        deployment env only, never the request -
                                        it widens the sandbox, so the operator
                                        who owns the deployment's confinement
                                        decides, not the directive author
    EVOLUTION_WORKDIR_ROOT               optional workdir root override
    EVOLUTION_SETUP_COMMAND             optional, shell-quoted argv run in the
                                        workdir after the overlay venv, WITH
                                        network (e.g. ".venv/bin/pip install -e .
                                        --no-deps"); runs build hooks outside the
                                        sandbox - trusted targets only
    EVOLUTION_SETUP_TIMEOUT              optional, timeout for EVOLUTION_SETUP_COMMAND (s)

A malformed EVOLUTION_SETUP_TIMEOUT fails the run with a named
ExtensionSetupError rather than a raw ValueError from inside the toolkit.
"""

from __future__ import annotations

import logging
import os
import shlex
from typing import TYPE_CHECKING, Optional

from pydantic import ValidationError

from aion.toolkits.behaviour_evolution import (
    BRANCH_STRATEGIES,
    CodexConfig,
    EvolutionConfig,
    EvolutionDirective,
    EvolutionWorker,
    LocalAccess,
    RemoteAccess,
    RemoteCredentials,
    TargetContext,
    build_tools,
)

from .directive import ParsedDirective
from .errors import ExtensionSetupError, UnsupportedDirectiveError
from .provider import CUSTOM, LOCAL_SESSION, resolve_provider, warn_ignored_keys

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aion.core.a2a.extensions.behaviour_evolution import ModelPreferences, RunLimits
    from aion.core.a2a.extensions.daemon import DaemonExtensionPayload

__all__ = [
    "LLM_CONFIG_KEY",
    "SPECS_ROOT_CONFIG_KEY",
    "build_worker",
    "check_environment",
]

# aion.yaml configuration fields; the control plane parameterizes them back
# into daemon requests as
# DaemonExtensionPayload.environment.configuration_variables[<key>].
LLM_CONFIG_KEY = "llm"
SPECS_ROOT_CONFIG_KEY = "evolution_specs_root"

# Kept only as the fallback default for directives that predate `branch_strategy`
# on the payload; new callers set the field directly. Not exported: nothing
# outside this module resolves branch strategy from a daemon config var anymore.
_BRANCH_STRATEGY_CONFIG_KEY = "evolution_branch_strategy"


def check_environment(daemon: Optional["DaemonExtensionPayload"]) -> None:
    """The subset of build_worker()'s checks that need only `daemon`, not a
    parsed directive - safe to run as a per-request preflight before a task
    exists (see EvolutionTaskHandler.preflight). Deliberately duplicated
    rather than factored out of build_worker(): build_worker() still runs its
    own copy of each check regardless, since not every caller goes through
    preflight first (a resumed task, a test calling build_worker directly),
    and re-checking is cheap and closes the race where env changes between
    the two calls.

    Raises:
        ExtensionSetupError: same conditions as build_worker() for the parts
            that do not depend on the directive.
    """
    if not os.environ.get("GITHUB_TOKEN"):
        raise ExtensionSetupError("GITHUB_TOKEN is not set - required to push the evolution branch")

    provider = resolve_provider()
    if provider == CUSTOM and not os.environ.get("CODEX_BASE_URL"):
        raise ExtensionSetupError("CODEX_BASE_URL is not set - required by CODEX_PROVIDER=custom")
    if provider not in (LOCAL_SESSION, CUSTOM) and _daemon_principal_selector(daemon) is None:
        raise ExtensionSetupError(
            "daemon request carries no environment.daemonAgentIdentityId - "
            "model usage cannot be attributed to a principal"
        )


def build_worker(
    parsed: ParsedDirective,
    daemon: Optional["DaemonExtensionPayload"],
) -> EvolutionWorker:
    """Wire a ready-to-run EvolutionWorker for a validated directive.

    Args:
        parsed: Validated directive off the routed request.
        daemon: The request's verified daemon payload - source of the model
            name and of the principal that model usage is attributed to.

    Raises:
        ExtensionSetupError: required environment is missing, or the directive is not
            supported by the installed toolkit version.
    """
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise ExtensionSetupError("GITHUB_TOKEN is not set - required to push the evolution branch")

    target = parsed.payload.target
    try:
        directive = EvolutionDirective(
            instruction=parsed.instruction,
            context_id=parsed.context_id,
            kind=parsed.payload.kind,
            mode=parsed.payload.mode,
            # scope arrives on the wire from the distributor.
            scope=parsed.scope,
            target=TargetContext(
                repo_url=target.repo_url,
                base_ref=target.base_ref,
            ),
        )
    except ValidationError as ex:
        # aion.core's directive contract can advertise kind/mode/scope values
        # (e.g. mode="directive", kind="bugfix") ahead of the installed toolkit
        # version that actually implements them. Reconcile that capability gap
        # here with a message naming the field, the requested value, and what
        # this deployment's toolkit accepts — instead of leaking the raw
        # pydantic error. Auto-adapts when the toolkit widens its Literals.
        #
        # UnsupportedDirectiveError (not the bare setup error) so this detail
        # survives the narrowing every other setup failure gets: the caller is
        # the only party who can route around a capability gap.
        raise UnsupportedDirectiveError(_unsupported_directive_message(ex)) from ex

    config_kwargs = {}
    specs_root = os.environ.get("EVOLUTION_SPECS_ROOT") or _daemon_config_var(
        daemon, SPECS_ROOT_CONFIG_KEY
    )
    if specs_root:
        config_kwargs["specs_root"] = specs_root

    limits = parsed.payload.limits
    config = EvolutionConfig(
        branch_strategy=_branch_strategy(daemon, parsed.payload.branch_strategy),
        workdir_root=os.environ.get("EVOLUTION_WORKDIR_ROOT"),
        executor_network=_env_flag("EVOLUTION_EXECUTOR_NETWORK"),
        # `codex_timeout` falls back to `op_timeout` inside the toolkit when
        # unset — see EvolutionConfig. Sourced from the directive's own
        # `limits`, not env: this deployment protects itself elsewhere (network
        # access, setup command); a run's own timeouts/budget are the caller's
        # protection for its own run, not a boundary this deployment enforces.
        op_timeout=_limit(limits, "op_timeout"),
        network_timeout=_limit(limits, "network_timeout"),
        codex_timeout=_limit(limits, "codex_timeout"),
        setup_command=_env_argv("EVOLUTION_SETUP_COMMAND"),
        setup_timeout=_env_float("EVOLUTION_SETUP_TIMEOUT"),
        **config_kwargs,
    )

    codex_config, credentials_provider = _codex_access(daemon, parsed.payload.model, limits)

    async def _token(_repo_url: str) -> str:
        # Repo-independent BY DESIGN, not by oversight. `GITHUB_TOKEN` is
        # expected to be scoped to the one repository this deployment serves,
        # so the token's own scope *is* the authorization: a directive naming
        # any other repository fails at the forge, not at a check here. Two
        # things follow, and both matter if that assumption ever changes:
        #
        #   * One agent, one repository. A deployment serving several targets
        #     needs a token spanning all of them, and the choice of target
        #     would then be back in the caller's hands, unguarded.
        #   * Widening the token (an org-wide installation, a classic PAT with
        #     `repo`) silently removes the guarantee. Nothing here would fail.
        #
        # The eventual fix is to stop taking the target from the payload at
        # all — resolve it from the authenticated deployment — at which point
        # this provider gets a real per-repository token and the argument.
        #
        # The toolkit injects this inline into git network calls only; it is
        # never stored on instances, written to .git/config, or exported.
        return github_token

    tools = build_tools(
        config,
        codex_config=codex_config,
        token_provider=_token,
        credentials_provider=credentials_provider,
    )
    return EvolutionWorker(directive, config, tools=tools)


def _unsupported_directive_message(ex: ValidationError) -> str:
    """Turn a toolkit directive-validation error into an actionable message.

    Names each rejected field with the value that was requested and what the
    installed toolkit accepts, so an operator sees e.g. "mode='directive'
    (installed toolkit accepts: 'advisory')" rather than a raw pydantic dump.
    Non-literal errors (should not happen for a well-formed payload) fall back
    to pydantic's own message for that field.
    """
    parts: list[str] = []
    for err in ex.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        if err.get("type") == "literal_error":
            requested = err.get("input")
            accepted = err.get("ctx", {}).get("expected")
            detail = f"{field}={requested!r}"
            if accepted:
                detail += f" (installed toolkit accepts: {accepted})"
            parts.append(detail)
        else:
            parts.append(f"{field}: {err.get('msg')}")
    joined = "; ".join(parts) if parts else str(ex)
    return (
        "directive uses a value the installed behaviour-evolution toolkit does "
        f"not support: {joined}"
    )


def _codex_access(daemon, prefs: Optional["ModelPreferences"], limits: Optional["RunLimits"]):
    """Resolve the Codex model_access/model and, when the provider authenticates,
    a credentials resolver.

    `CODEX_PROVIDER` picks one of three mutually exclusive modes:

      * `aion` - the Aion model service. Endpoint comes from api settings, a
        fresh short-lived JWT is minted per call, and the daemon identity is
        attached as the principal so usage is attributed. Takes no operator
        knobs at all.
      * `local_session` - the operator's own logged-in Codex CLI session
        (`auth.json`, optionally from `CODEX_HOME`). No endpoint, no key; usage
        counts against their personal subscription.
      * `custom` - any OpenAI-Responses-compatible endpoint. `CODEX_BASE_URL`
        is required; `CODEX_API_KEY` is optional - present means the secret
        rides `Authorization: Bearer`, absent means an unauthenticated endpoint
        (e.g. a local Ollama server). No principal either way.

    There is no default provider: see `resolve_provider`.

    `prefs` is the directive's optional `model` field: per-run tuning for
    `model`/`model_reasoning_effort`/`model_context_window`, resolved as
    `prefs` -> `llm` config var -> toolkit default. `limits.max_total_tokens`
    is the caller's own token budget for the run, taken as-is - there is no
    deployment-side ceiling to fall back to or clamp against here (unlike
    `model_catalog_json`/`codex_bin` below, which stay env-only because they
    name a host filesystem path and an executable, and provider selection,
    which decides whose credentials and quota pay for the call - all
    deployment-operator decisions, not directive-author ones).
    """
    provider = resolve_provider()
    warn_ignored_keys(provider)
    credentials_provider = None

    if provider == LOCAL_SESSION:
        model_access = LocalAccess(home=os.environ.get("CODEX_HOME"))

    elif provider == CUSTOM:
        base_url = os.environ.get("CODEX_BASE_URL")
        if not base_url:
            raise ExtensionSetupError(
                "CODEX_BASE_URL is not set - required by CODEX_PROVIDER=custom"
            )
        model_access = RemoteAccess(base_url=base_url)
        api_key = os.environ.get("CODEX_API_KEY")
        if api_key:
            # No principal: a plain API-key endpoint has nothing to attribute
            # usage to, so no attribution header is emitted (see the toolkit's
            # RemoteAccess.principal_header). Unlike the Aion JWT this secret is
            # long-lived by nature - it is read once here and only ever reaches
            # the Codex subprocess environment, never the parent's.
            async def credentials_provider() -> RemoteCredentials:
                return RemoteCredentials(secret=api_key)

    else:  # AION
        # Imported lazily: the other providers never touch api settings/JWT
        # infrastructure.
        from aion.api.control_plane import AION_PRINCIPAL_SELECTOR_HEADER
        from aion.api.model_service_client import aion_jwt_api_key, aion_model_base_url

        principal = _daemon_principal_selector(daemon)
        if principal is None:
            raise ExtensionSetupError(
                "daemon request carries no environment.daemonAgentIdentityId - "
                "model usage cannot be attributed to a principal"
            )

        # TODO(2026-08-27): aion_model_base_url() resolves to AION_API_HOST,
        # the same host used for the platform's GraphQL/WS traffic - there is
        # no endpoint of its own carved out for Codex model calls yet, and no
        # AION_API_HOST has been provisioned/agreed for this deployment. This
        # provider has nothing to talk to until that's resolved - see the
        # module docstring. Not a code bug: the wiring below is correct once
        # the URL exists.
        model_access = RemoteAccess(
            base_url=aion_model_base_url(),
            principal_header=AION_PRINCIPAL_SELECTOR_HEADER,
        )

        async def credentials_provider() -> RemoteCredentials:
            # Secret is minted per Codex call and never stored; principal
            # attributes the usage to the agent's daemon identity (policy
            # enforcement lands on the service side later).
            return RemoteCredentials(secret=await aion_jwt_api_key(), principal=principal)

    codex_config = CodexConfig(
        model_access=model_access,
        model=_pref(prefs, "name") or _daemon_model(daemon),
        model_catalog_json=os.environ.get("CODEX_MODEL_CATALOG_JSON"),
        model_reasoning_effort=_pref(prefs, "reasoning_effort"),
        model_context_window=_pref(prefs, "context_window"),
        # Token budget for this run: on reaching it the executor stops
        # gracefully at the next turn boundary and the run still delivers what
        # was committed (COMPLETED, not FAILED) — see the toolkit's
        # CodexConfig.max_total_tokens. From the directive's `limits`, not env.
        max_total_tokens=_limit(limits, "max_total_tokens"),
        codex_bin=os.environ.get("CODEX_BIN", "codex"),
    )
    return codex_config, credentials_provider


def _pref(prefs: Optional["ModelPreferences"], field: str):
    """One directive-supplied model preference; None when the directive omitted it."""
    return None if prefs is None else getattr(prefs, field)


def _limit(limits: Optional["RunLimits"], field: str):
    """One directive-supplied run limit; None when the directive omitted it."""
    return None if limits is None else getattr(limits, field)


def _daemon_model(daemon) -> Optional[str]:
    """Model name from the daemon environment's ``llm`` configuration variable."""
    return _daemon_config_var(daemon, LLM_CONFIG_KEY)


def _branch_strategy(daemon, requested: Optional[str]) -> str:
    """Branch strategy: directive's own field -> daemon config var -> default.

    The daemon config var is kept only as the fallback for directives that
    predate `branch_strategy` on the payload - new callers set the field
    directly, so this deployment never needs an env override for it.

    Raises:
        ExtensionSetupError: the configured value is not a strategy the toolkit knows.
    """
    value = requested or _daemon_config_var(daemon, _BRANCH_STRATEGY_CONFIG_KEY) or "beta-branch"
    if value not in BRANCH_STRATEGIES:
        raise ExtensionSetupError(
            f"unsupported evolution branch strategy {value!r} - "
            f"expected one of {list(BRANCH_STRATEGIES)}"
        )
    return value


_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _env_flag(name: str) -> bool:
    """A boolean deployment env var: "1"/"true"/"yes"/"on" (any case) is True.

    A non-empty value that is neither truthy nor a recognized falsy spelling
    (e.g. a typo) is treated as False, same as unset — but logged, so a
    misspelled flag does not silently disable what the operator meant to
    enable.
    """
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw and raw not in _FALSY:
        log.warning(
            "%s=%r is not a recognized boolean value (expected one of %s) - treating as false",
            name,
            os.environ.get(name),
            ", ".join(_TRUTHY),
        )
    return False


def _env_float(name: str) -> Optional[float]:
    """A numeric deployment env var (seconds); None when unset/blank.

    A malformed value fails the run here with a named error, rather than
    surfacing as a raw ValueError from deep inside the toolkit.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError as ex:
        raise ExtensionSetupError(f"{name} must be a number of seconds, got {raw!r}") from ex


def _env_argv(name: str) -> Optional[list[str]]:
    """A shell-quoted command line split into argv; None when unset/blank.

    Parsed with `shlex` so an operator can write
    `EVOLUTION_SETUP_COMMAND=".venv/bin/pip install -e . --no-deps"`. An
    unbalanced-quote value fails the run here with a named error.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return shlex.split(raw)
    except ValueError as ex:
        raise ExtensionSetupError(f"{name} is not a valid command line: {ex}") from ex


def _daemon_config_var(daemon, key: str) -> Optional[str]:
    """A configuration variable from the request's daemon environment."""
    if daemon is None:
        return None
    return daemon.environment.configuration_variables.get(key)


def _daemon_principal_selector(daemon) -> Optional[str]:
    """Header-ready identity selector for the agent's daemon identity."""
    if daemon is None:
        return None
    identity_id = daemon.environment.daemon_agent_identity_id
    if not identity_id:
        return None
    return f"aion://agent/identity/{identity_id}"
