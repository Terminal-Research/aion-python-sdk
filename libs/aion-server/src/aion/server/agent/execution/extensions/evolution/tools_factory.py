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
control plane) name the Codex model (``llm``) and the branch strategy
(``evolution_branch_strategy``). Env vars for those exist only as local-dev
overrides. Deployment-scoped concerns (git credentials, binaries, workdir)
stay in the environment - GITHUB_TOKEN in particular never comes from the
request: a token in the directive would leak into Codex prompts and logs.

Env:
    CODEX_BASE_URL                      optional endpoint override (e.g. ollama);
                                        set = no Aion token resolver attached
    CODEX_MODEL                         optional model override
    CODEX_MODEL_CATALOG_JSON            optional catalog for local models
    CODEX_MODEL_REASONING_EFFORT        optional
    CODEX_MODEL_CONTEXT_WINDOW          optional, int
    CODEX_BIN                           optional, default "codex"
    GITHUB_TOKEN                        required, injected JIT into git calls
    EVOLUTION_BRANCH_STRATEGY           optional strategy override
    EVOLUTION_SPECS_ROOT                optional spec-convention root override
    EVOLUTION_EXECUTOR_NETWORK          optional, "1"/"true" grants the executor
                                        sandbox network access (installs allowed,
                                        briefing demands manifest declaration);
                                        deployment env only, never the request -
                                        it widens the sandbox, so the operator
                                        who owns the deployment's confinement
                                        decides, not the directive author
    BEHAVIOUR_EVOLUTION_WORKDIR_ROOT    optional workdir root override
    EVOLUTION_OP_TIMEOUT_S              optional, per-subprocess-op timeout (s)
    EVOLUTION_NETWORK_TIMEOUT_S         optional, timeout for network git ops (s)
    EVOLUTION_CODEX_TIMEOUT_S           optional, wall-clock ceiling for one whole
                                        codex exec run (s); falls back to
                                        EVOLUTION_OP_TIMEOUT_S when unset
    EVOLUTION_MAX_TOTAL_TOKENS          optional, int per-call token budget; on
                                        reaching it the executor stops gracefully
                                        and the run still delivers what's committed
                                        (COMPLETED, not FAILED)
    EVOLUTION_SETUP_COMMAND             optional, shell-quoted argv run in the
                                        workdir after the overlay venv, WITH
                                        network (e.g. ".venv/bin/pip install -e .
                                        --no-deps"); runs build hooks outside the
                                        sandbox - trusted targets only
    EVOLUTION_SETUP_TIMEOUT_S           optional, timeout for EVOLUTION_SETUP_COMMAND (s)

All timeout/budget vars are deployment env only (never the request), and a
malformed value fails the run with a named ExtensionSetupError rather than a
raw ValueError from inside the toolkit.
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
    ModelServiceCredentials,
    TargetContext,
    build_tools,
)

from .directive import ParsedDirective
from .errors import ExtensionSetupError

if TYPE_CHECKING:
    from aion.core.a2a.extensions.daemon import DaemonExtensionPayload

__all__ = [
    "BRANCH_STRATEGY_CONFIG_KEY",
    "LLM_CONFIG_KEY",
    "SPECS_ROOT_CONFIG_KEY",
    "build_worker",
]

# aion.yaml configuration fields; the control plane parameterizes them back
# into daemon requests as
# DaemonExtensionPayload.environment.configuration_variables[<key>].
LLM_CONFIG_KEY = "llm"
BRANCH_STRATEGY_CONFIG_KEY = "evolution_branch_strategy"
SPECS_ROOT_CONFIG_KEY = "evolution_specs_root"


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
            # stage arrives on the wire from the distributor.
            stage=parsed.stage,
            target=TargetContext(
                repo_url=target.repo_url,
                base_ref=target.base_ref,
                target_version_id=target.target_version_id,
            ),
        )
    except ValidationError as ex:
        # aion-core's directive contract can advertise kind/mode/stage values
        # (e.g. mode="directive", kind="bugfix") ahead of the installed toolkit
        # version that actually implements them. Reconcile that capability gap
        # here with a message naming the field, the requested value, and what
        # this deployment's toolkit accepts — instead of leaking the raw
        # pydantic error. Auto-adapts when the toolkit widens its Literals.
        raise ExtensionSetupError(_unsupported_directive_message(ex)) from ex

    config_kwargs = {}
    specs_root = os.environ.get("EVOLUTION_SPECS_ROOT") or _daemon_config_var(
        daemon, SPECS_ROOT_CONFIG_KEY
    )
    if specs_root:
        config_kwargs["specs_root"] = specs_root

    config = EvolutionConfig(
        branch_strategy=_branch_strategy(daemon),
        workdir_root=os.environ.get("BEHAVIOUR_EVOLUTION_WORKDIR_ROOT"),
        executor_network=_env_flag("EVOLUTION_EXECUTOR_NETWORK"),
        # Operator-facing timeout/setup knobs (deployment env only). Left unset
        # they keep the toolkit defaults (no limit); a malformed value fails the
        # run here with a named error rather than a raw ValueError deep in the
        # toolkit. `codex_timeout_s` falls back to `op_timeout_s` inside the
        # toolkit when unset — see EvolutionConfig.
        op_timeout_s=_env_float("EVOLUTION_OP_TIMEOUT_S"),
        network_timeout_s=_env_float("EVOLUTION_NETWORK_TIMEOUT_S"),
        codex_timeout_s=_env_float("EVOLUTION_CODEX_TIMEOUT_S"),
        setup_command=_env_argv("EVOLUTION_SETUP_COMMAND"),
        setup_timeout_s=_env_float("EVOLUTION_SETUP_TIMEOUT_S"),
        **config_kwargs,
    )

    codex_config, credentials_provider = _codex_access(daemon)

    async def _token(_repo_url: str) -> str:
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


def _codex_access(daemon):
    """Resolve the Codex endpoint/model and, for the Aion model service,
    a token resolver; an overridden endpoint gets no resolver at all."""
    base_url = os.environ.get("CODEX_BASE_URL")
    credentials_provider = None
    codex_kwargs = {}

    if not base_url:
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

        base_url = aion_model_base_url()
        codex_kwargs["principal_header"] = AION_PRINCIPAL_SELECTOR_HEADER

        async def credentials_provider() -> ModelServiceCredentials:
            # Secret is minted per Codex call and never stored; principal
            # attributes the usage to the agent's daemon identity (policy
            # enforcement lands on the service side later).
            return ModelServiceCredentials(secret=await aion_jwt_api_key(), principal=principal)

    codex_config = CodexConfig(
        base_url=base_url,
        model=os.environ.get("CODEX_MODEL") or _daemon_model(daemon),
        model_catalog_json=os.environ.get("CODEX_MODEL_CATALOG_JSON"),
        model_reasoning_effort=os.environ.get("CODEX_MODEL_REASONING_EFFORT"),
        model_context_window=_env_int("CODEX_MODEL_CONTEXT_WINDOW"),
        # Per-call token budget: on reaching it the executor is stopped
        # gracefully at the next turn boundary and the run still delivers what
        # was committed (COMPLETED, not FAILED) — see the toolkit's
        # CodexConfig.max_total_tokens. Deployment env only.
        max_total_tokens=_env_int("EVOLUTION_MAX_TOTAL_TOKENS"),
        codex_bin=os.environ.get("CODEX_BIN", "codex"),
        **codex_kwargs,
    )
    return codex_config, credentials_provider


def _daemon_model(daemon) -> Optional[str]:
    """Model name from the daemon environment's ``llm`` configuration variable."""
    return _daemon_config_var(daemon, LLM_CONFIG_KEY)


def _branch_strategy(daemon) -> str:
    """Branch strategy: local env override -> request config var -> default.

    Raises:
        ExtensionSetupError: the configured value is not a strategy the toolkit knows.
    """
    value = (
        os.environ.get("EVOLUTION_BRANCH_STRATEGY")
        or _daemon_config_var(daemon, BRANCH_STRATEGY_CONFIG_KEY)
        or "beta-branch"
    )
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


def _env_int(name: str) -> Optional[int]:
    """An integer deployment env var; None when unset/blank."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError as ex:
        raise ExtensionSetupError(f"{name} must be an integer, got {raw!r}") from ex


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
