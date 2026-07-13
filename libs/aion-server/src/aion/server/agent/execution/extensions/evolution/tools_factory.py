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
    BEHAVIOUR_EVOLUTION_WORKDIR_ROOT    optional workdir root override
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from pydantic import ValidationError

from aion.toolkits.behaviour_evolution import (
    CodexConfig,
    EvolutionConfig,
    EvolutionDirective,
    EvolutionWorker,
    ModelServiceCredentials,
    TargetContext,
    build_tools,
)

from .directive import ParsedDirective
from .errors import SetupError

if TYPE_CHECKING:
    from aion.core.a2a.extensions.daemon import DaemonExtensionPayload

__all__ = ["BRANCH_STRATEGY_CONFIG_KEY", "LLM_CONFIG_KEY", "build_worker"]

# aion.yaml configuration fields; the control plane parameterizes them back
# into daemon requests as
# DaemonExtensionPayload.environment.configuration_variables[<key>].
LLM_CONFIG_KEY = "llm"
BRANCH_STRATEGY_CONFIG_KEY = "evolution_branch_strategy"

_BRANCH_STRATEGIES = ("beta-branch", "pull-request")


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
        SetupError: required environment is missing, or the directive is not
            supported by the installed toolkit version.
    """
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise SetupError("GITHUB_TOKEN is not set - required to push the evolution branch")

    target = parsed.payload.target
    try:
        directive = EvolutionDirective(
            instruction=parsed.instruction,
            kind=parsed.payload.kind,
            mode=parsed.payload.mode,
            target=TargetContext(
                repo_url=target.repo_url,
                base_ref=target.base_ref,
                target_version_id=target.target_version_id,
            ),
        )
    except ValidationError as ex:
        # E.g. a directive with kind/mode values newer than the installed
        # toolkit's Literal types supports.
        raise SetupError(f"directive is not supported by the installed toolkit: {ex}") from ex

    config = EvolutionConfig(
        branch_strategy=_branch_strategy(daemon),
        workdir_root=os.environ.get("BEHAVIOUR_EVOLUTION_WORKDIR_ROOT"),
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
        repo_url=target.repo_url,
    )
    return EvolutionWorker(directive, config, tools=tools)


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
            raise SetupError(
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

    context_window = os.environ.get("CODEX_MODEL_CONTEXT_WINDOW")
    codex_config = CodexConfig(
        base_url=base_url,
        model=os.environ.get("CODEX_MODEL") or _daemon_model(daemon),
        model_catalog_json=os.environ.get("CODEX_MODEL_CATALOG_JSON"),
        model_reasoning_effort=os.environ.get("CODEX_MODEL_REASONING_EFFORT"),
        model_context_window=int(context_window) if context_window else None,
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
        SetupError: the configured value is not a strategy the toolkit knows.
    """
    value = (
        os.environ.get("EVOLUTION_BRANCH_STRATEGY")
        or _daemon_config_var(daemon, BRANCH_STRATEGY_CONFIG_KEY)
        or "beta-branch"
    )
    if value not in _BRANCH_STRATEGIES:
        raise SetupError(
            f"unsupported evolution branch strategy {value!r} - "
            f"expected one of {list(_BRANCH_STRATEGIES)}"
        )
    return value


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
