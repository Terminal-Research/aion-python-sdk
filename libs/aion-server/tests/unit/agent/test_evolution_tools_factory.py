"""Tests for the request/env -> EvolutionWorker factory.

Auto-skipped when the optional aion-toolkit-behaviour-evolution-python
distribution is not installed (it is not a dependency of aion-server); run
them in an e2e-capable environment with the toolkit pip-installed.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("aion.toolkits.behaviour_evolution")

from aion.core.a2a.extensions.behaviour_evolution import (  # noqa: E402
    EvolutionDirectiveEventPayload,
    TargetContext,
)
from aion.server.agent.execution.extensions.evolution.directive import ParsedDirective  # noqa: E402
from aion.server.agent.execution.extensions.evolution.errors import SetupError  # noqa: E402
from aion.server.agent.execution.extensions.evolution.tools_factory import build_worker  # noqa: E402

REPO_URL = "https://github.com/acme/target-agent.git"


def _parsed(kind: str = "feature", mode: str = "advisory") -> ParsedDirective:
    return ParsedDirective(
        instruction="Append a friendly sentence to README.md.",
        payload=EvolutionDirectiveEventPayload(
            target=TargetContext(repo_url=REPO_URL, base_ref="HEAD", target_version_id="v-1"),
            kind=kind,
            mode=mode,
        ),
    )


def _daemon(
    llm: str | None = "qwen",
    identity_id: str | None = "daemon-1",
    branch_strategy: str | None = None,
):
    config_vars = {}
    if llm:
        config_vars["llm"] = llm
    if branch_strategy:
        config_vars["evolution_branch_strategy"] = branch_strategy
    return SimpleNamespace(
        environment=SimpleNamespace(
            configuration_variables=config_vars,
            daemon_agent_identity_id=identity_id,
        )
    )


def _set_env(monkeypatch, **overrides):
    values = {
        "CODEX_BASE_URL": "http://127.0.0.1:11434/v1",
        "GITHUB_TOKEN": "test-token",
    }
    values.update(overrides)
    for key in (
        "CODEX_BASE_URL",
        "CODEX_MODEL",
        "GITHUB_TOKEN",
        "EVOLUTION_BRANCH_STRATEGY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        if value is not None:
            monkeypatch.setenv(key, value)


class TestBuildWorker:
    def test_missing_github_token_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, GITHUB_TOKEN=None)
        with pytest.raises(SetupError, match="GITHUB_TOKEN"):
            build_worker(_parsed(), _daemon())

    def test_builds_worker_with_directive_mapped(self, monkeypatch):
        from aion.toolkits.behaviour_evolution import EvolutionWorker

        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon())

        assert isinstance(worker, EvolutionWorker)

    def test_overridden_endpoint_attaches_no_token_resolver(self, monkeypatch):
        """The wrapper chooses the mode: an explicit CODEX_BASE_URL means a
        local/foreign endpoint, so no Aion credentials provider is wired and
        no daemon identity is required."""
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon(identity_id=None))

        codex = worker._tools.codex
        assert codex.config.base_url == "http://127.0.0.1:11434/v1"
        assert codex._credentials_provider is None

    def test_daemon_llm_variable_picks_model_when_env_unset(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon(llm="qwen"))

        assert worker._tools.codex.config.model == "qwen"

    def test_env_model_overrides_daemon_variable(self, monkeypatch):
        _set_env(monkeypatch, CODEX_MODEL="gpt-oss:20b")
        worker = build_worker(_parsed(), _daemon(llm="qwen"))

        assert worker._tools.codex.config.model == "gpt-oss:20b"

    def test_defaults_to_aion_model_service_with_token_resolver(self, monkeypatch):
        from aion.api.control_plane import AION_PRINCIPAL_SELECTOR_HEADER

        _set_env(monkeypatch, CODEX_BASE_URL=None)
        monkeypatch.setattr(
            "aion.api.model_service_client.aion_model_base_url",
            lambda: "https://api.aion.example/v1",
        )
        worker = build_worker(_parsed(), _daemon(llm="qwen"))

        codex = worker._tools.codex
        assert codex.config.base_url == "https://api.aion.example/v1"
        assert codex.config.model == "qwen"
        assert codex.config.principal_header == AION_PRINCIPAL_SELECTOR_HEADER
        assert codex._credentials_provider is not None

    def test_aion_mode_requires_daemon_identity(self, monkeypatch):
        """Going to the model service without a principal would leave usage
        unattributed - reject before the run starts."""
        _set_env(monkeypatch, CODEX_BASE_URL=None)
        with pytest.raises(SetupError, match="daemonAgentIdentityId"):
            build_worker(_parsed(), _daemon(identity_id=None))

    def test_branch_strategy_from_daemon_config_var(self, monkeypatch):
        """pull-request strategy is observable through the wired forge client."""
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon(branch_strategy="pull-request"))
        assert worker._tools.forge is not None

        worker = build_worker(_parsed(), _daemon())  # default beta-branch
        assert worker._tools.forge is None

    def test_env_branch_strategy_overrides_daemon_variable(self, monkeypatch):
        _set_env(monkeypatch, EVOLUTION_BRANCH_STRATEGY="pull-request")
        worker = build_worker(_parsed(), _daemon(branch_strategy="beta-branch"))
        assert worker._tools.forge is not None

    def test_unknown_branch_strategy_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch)
        with pytest.raises(SetupError, match="unsupported evolution branch strategy"):
            build_worker(_parsed(), _daemon(branch_strategy="direct-push"))

    def test_unsupported_directive_values_surface_as_setup_error(self, monkeypatch):
        """The core payload allows kind/mode values the installed toolkit may
        not support yet (e.g. kind='bugfix' vs the toolkit's Literal['feature']);
        that mismatch must fail the task with a clear message, not a traceback."""
        from aion.toolkits.behaviour_evolution.models.directive import Kind

        _set_env(monkeypatch)
        if "bugfix" in getattr(Kind, "__args__", ()):
            pytest.skip("installed toolkit already supports kind='bugfix'")

        with pytest.raises(SetupError, match="not supported by the installed toolkit"):
            build_worker(_parsed(kind="bugfix"), _daemon())
