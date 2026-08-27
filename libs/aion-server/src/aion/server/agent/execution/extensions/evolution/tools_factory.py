"""Lazy toolkit boundary: request + environment config -> EvolutionWorker.

The only module in the evolution package that imports the optional
aion-toolkit-behaviour-evolution-python distribution - handler.py imports it
lazily inside stream(), so aion-server keeps no hard dependency on the
toolkit.

Codex credentials follow the toolkit's own DI: ``credentials_provider`` is
optional, and this wrapper decides whether to attach one. By default Codex
goes to the Aion model service (aion-api-client's ``aion_model_base_url()``)
with a token resolver that mints a fresh short-lived Aion JWT per call plus
the agent's daemon-identity principal (``Aion-Principal-Selector`` header,
from the request's ``environment.daemon_agent_identity_id``) - secret and
principal travel per-call and are never stored. Setting CODEX_BASE_URL
points Codex at any OpenAI-compatible server (e.g. local ollama) instead,
with no token resolver at all.

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
    CODEX_BASE_URL                      optional endpoint override (e.g. ollama);
                                        set = no Aion token resolver attached;
                                        the literal value "local" (case-insensitive)
                                        instead uses the operator's own logged-in
                                        Codex CLI session (~/.codex/auth.json) -
                                        usage counts against their personal
                                        subscription limits, not this deployment's
                                        Aion model service quota or any API key
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
    ModelServiceCredentials,
    RemoteAccess,
    TargetContext,
    build_tools,
)

from .directive import ParsedDirective
from .errors import ExtensionSetupError, UnsupportedDirectiveError

if TYPE_CHECKING:
    from aion.core.a2a.extensions.behaviour_evolution import ModelPreferences, RunLimits
    from aion.core.a2a.extensions.daemon import DaemonExtensionPayload

__all__ = [
    "LLM_CONFIG_KEY",
    "SPECS_ROOT_CONFIG_KEY",
    "build_worker",
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
        # aion-core's directive contract can advertise kind/mode/scope values
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
    """Resolve the Codex model_access/model and, for the Aion model service,
    a token resolver; an overridden endpoint gets no resolver at all.

    CODEX_BASE_URL="local" (case-insensitive) is a third mode, checked first:
    it resolves to a `LocalAccess` instead of any `RemoteAccess`, in favor of
    the operator's own logged-in Codex CLI session - no Aion JWT, no API key,
    no control-plane import at all.

    `prefs` is the directive's optional `model` field: per-run tuning for
    `model`/`model_reasoning_effort`/`model_context_window`, resolved as
    `prefs` -> `llm` config var -> toolkit default. `limits.max_total_tokens`
    is the caller's own token budget for the run, taken as-is - there is no
    deployment-side ceiling to fall back to or clamp against here (unlike
    `model_catalog_json`/`codex_bin` below, which stay env-only because they
    name a host filesystem path and an executable, and `CODEX_BASE_URL`/mode
    selection, which decides whose credentials and quota pay for the call -
    all deployment-operator decisions, not directive-author ones).
    """
    raw_base_url = os.environ.get("CODEX_BASE_URL")
    credentials_provider = None

    if raw_base_url and raw_base_url.strip().lower() == "local":
        model_access = LocalAccess()
    elif not raw_base_url:
        # Aion model service. Imported lazily: an overridden endpoint never
        # touches api settings/JWT infrastructure.
        from aion.api.control_plane import AION_PRINCIPAL_SELECTOR_HEADER
        from aion.api.model_service_client import aion_jwt_api_key, aion_model_base_url

        principal = _daemon_principal_selector(daemon)
        if principal is None:
            raise ExtensionSetupError(
                "daemon request carries no environment.daemonAgentIdentityId - "
                "model usage cannot be attributed to a principal"
            )

        model_access = RemoteAccess(
            base_url=aion_model_base_url(),
            principal_header=AION_PRINCIPAL_SELECTOR_HEADER,
        )

        async def credentials_provider() -> ModelServiceCredentials:
            # Secret is minted per Codex call and never stored; principal
            # attributes the usage to the agent's daemon identity (policy
            # enforcement lands on the service side later).
            return ModelServiceCredentials(secret=await aion_jwt_api_key(), principal=principal)

    else:
        model_access = RemoteAccess(base_url=raw_base_url)

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


def _env_flag(name: str) -> bool:
    """A boolean deployment env var: "1"/"true"/"yes"/"on" (any case) is True."""
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


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
