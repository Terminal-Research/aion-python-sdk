"""Tests for the request/env -> EvolutionWorker factory.

Auto-skipped when the optional aion-toolkit-behaviour-evolution-python
distribution is not installed (no extra of this package pulls it in); run
them in an e2e-capable environment with the toolkit pip-installed.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("aion.toolkits.behaviour_evolution")

from aion.core.a2a.extensions.behaviour_evolution import (  # noqa: E402
    EvolutionDirectiveEventPayload,
    ModelPreferences,
    RunLimits,
    TargetContext,
)
from aion.server.agent.execution.extensions.evolution.directive import ParsedDirective  # noqa: E402
from aion.server.agent.execution.extensions.evolution.errors import ExtensionSetupError  # noqa: E402
from aion.server.agent.execution.extensions.evolution.tools_factory import (  # noqa: E402
    build_worker,
    check_environment,
)
from aion.toolkits.behaviour_evolution import LocalAccess, RemoteAccess  # noqa: E402

REPO_URL = "https://github.com/acme/target-agent.git"


def _parsed(
    kind: str = "feature",
    mode: str = "advisory",
    model: ModelPreferences | None = None,
    branch_strategy: str | None = None,
    limits: RunLimits | None = None,
) -> ParsedDirective:
    return ParsedDirective(
        instruction="Append a friendly sentence to README.md.",
        context_id="ctx-456",
        payload=EvolutionDirectiveEventPayload(
            target=TargetContext(repo_url=REPO_URL, base_ref="HEAD"),
            kind=kind,
            mode=mode,
            model=model,
            branch_strategy=branch_strategy,
            limits=limits,
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
        "CODEX_PROVIDER": "custom",
        "CODEX_BASE_URL": "http://127.0.0.1:11434/v1",
        "GITHUB_TOKEN": "test-token",
    }
    values.update(overrides)
    for key in (
        "CODEX_PROVIDER",
        "CODEX_BASE_URL",
        "CODEX_API_KEY",
        "CODEX_HOME",
        "CODEX_MODEL_CATALOG_JSON",
        "CODEX_BIN",
        "GITHUB_TOKEN",
        "EVOLUTION_SPECS_ROOT",
        "EVOLUTION_EXECUTOR_NETWORK",
        "EVOLUTION_WORKDIR_ROOT",
        "EVOLUTION_SETUP_COMMAND",
        "EVOLUTION_SETUP_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        if value is not None:
            monkeypatch.setenv(key, value)


class TestCheckEnvironment:
    """check_environment() is build_worker()'s per-request preflight subset
    (see EvolutionTaskHandler.preflight) - only the checks that need just
    `daemon`, not a parsed directive."""

    def test_passes_with_valid_environment(self, monkeypatch):
        _set_env(monkeypatch)
        check_environment(_daemon())  # must not raise

    def test_missing_github_token_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, GITHUB_TOKEN=None)
        with pytest.raises(ExtensionSetupError, match="GITHUB_TOKEN"):
            check_environment(_daemon())

    def test_missing_codex_provider_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, CODEX_PROVIDER=None)
        with pytest.raises(ExtensionSetupError, match="CODEX_PROVIDER"):
            check_environment(_daemon())

    def test_custom_provider_without_base_url_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, CODEX_BASE_URL=None)
        with pytest.raises(ExtensionSetupError, match="CODEX_BASE_URL"):
            check_environment(_daemon())

    def test_aion_provider_without_daemon_identity_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, CODEX_PROVIDER="aion", CODEX_BASE_URL=None)
        with pytest.raises(ExtensionSetupError, match="daemonAgentIdentityId"):
            check_environment(_daemon(identity_id=None))

    def test_aion_provider_with_daemon_identity_passes(self, monkeypatch):
        _set_env(monkeypatch, CODEX_PROVIDER="aion", CODEX_BASE_URL=None)
        check_environment(_daemon(identity_id="daemon-1"))  # must not raise

    def test_local_session_provider_needs_no_daemon(self, monkeypatch):
        _set_env(monkeypatch, CODEX_PROVIDER="local_session", CODEX_BASE_URL=None)
        check_environment(None)  # must not raise


class TestBuildWorker:
    def test_missing_github_token_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, GITHUB_TOKEN=None)
        with pytest.raises(ExtensionSetupError, match="GITHUB_TOKEN"):
            build_worker(_parsed(), _daemon())

    def test_builds_worker_with_directive_mapped(self, monkeypatch):
        from aion.toolkits.behaviour_evolution import EvolutionWorker

        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon())

        assert isinstance(worker, EvolutionWorker)
        assert worker._directive.context_id == "ctx-456"

    def test_specs_root_from_env_override(self, monkeypatch):
        _set_env(monkeypatch, EVOLUTION_SPECS_ROOT=".evolution-specs")
        worker = build_worker(_parsed(), _daemon())
        assert worker._config.specs_root == ".evolution-specs"

    def test_specs_root_from_daemon_config_var(self, monkeypatch):
        _set_env(monkeypatch)
        daemon = _daemon()
        daemon.environment.configuration_variables["evolution_specs_root"] = "docs/specs"
        worker = build_worker(_parsed(), daemon)
        assert worker._config.specs_root == "docs/specs"

    def test_executor_network_off_by_default(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon())
        assert worker._config.executor_network is False

    def test_executor_network_from_env_flag(self, monkeypatch):
        """Deployment env only — the directive must not be able to widen the
        sandbox; the flag reaches both the toolkit config and (via build_tools)
        the Codex sandbox grant."""
        _set_env(monkeypatch, EVOLUTION_EXECUTOR_NETWORK="true")
        worker = build_worker(_parsed(), _daemon())
        assert worker._config.executor_network is True
        assert worker._tools.codex.config.network_access is True

    def test_local_session_provider_uses_local_access(self, monkeypatch):
        """CODEX_PROVIDER=local_session resolves to a `LocalAccess`, not any
        `RemoteAccess`/credentials_provider, in favor of the operator's own
        logged-in Codex CLI session."""
        _set_env(monkeypatch, CODEX_PROVIDER="local_session", CODEX_BASE_URL=None)
        worker = build_worker(_parsed(model=ModelPreferences(name="gpt-5.1-codex")), _daemon())
        codex = worker._tools.codex
        assert codex.config.model_access == LocalAccess()
        assert codex.config.model == "gpt-5.1-codex"
        assert codex._credentials_provider is None

    def test_provider_value_is_case_insensitive(self, monkeypatch):
        _set_env(monkeypatch, CODEX_PROVIDER="Local_Session", CODEX_BASE_URL=None)
        worker = build_worker(_parsed(), _daemon())
        assert worker._tools.codex.config.model_access == LocalAccess()

    def test_local_session_reads_codex_home(self, monkeypatch):
        _set_env(
            monkeypatch,
            CODEX_PROVIDER="local_session",
            CODEX_BASE_URL=None,
            CODEX_HOME="/home/op/.codex-alt",
        )
        worker = build_worker(_parsed(), _daemon())
        assert worker._tools.codex.config.model_access == LocalAccess(home="/home/op/.codex-alt")

    def test_limits_default_to_toolkit_defaults_when_absent(self, monkeypatch):
        """A directive with no `limits` leaves every ceiling unset — the
        toolkit's own defaults (no limit) apply."""
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon())
        config = worker._config
        assert config.op_timeout is None
        assert config.network_timeout is None
        assert config.codex_timeout is None
        assert config.setup_command is None
        assert config.setup_timeout is None
        assert worker._tools.codex.config.max_total_tokens is None
        # No timeouts set -> codex client runs unbounded.
        assert worker._tools.codex._timeout is None

    def test_setup_command_is_shlex_split(self, monkeypatch):
        _set_env(
            monkeypatch,
            EVOLUTION_SETUP_COMMAND=".venv/bin/pip install -e . --no-deps",
            EVOLUTION_SETUP_TIMEOUT="120",
        )
        worker = build_worker(_parsed(), _daemon())
        assert worker._config.setup_command == [
            ".venv/bin/pip",
            "install",
            "-e",
            ".",
            "--no-deps",
        ]
        assert worker._config.setup_timeout == 120.0

    def test_malformed_setup_timeout_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, EVOLUTION_SETUP_TIMEOUT="soon")
        with pytest.raises(ExtensionSetupError, match="EVOLUTION_SETUP_TIMEOUT"):
            build_worker(_parsed(), _daemon())

    def test_custom_provider_without_api_key_attaches_no_credentials(self, monkeypatch):
        """`custom` with no CODEX_API_KEY means an unauthenticated endpoint
        (e.g. local Ollama): no credentials provider is wired and no daemon
        identity is required."""
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon(identity_id=None))

        codex = worker._tools.codex
        assert codex.config.model_access == RemoteAccess(base_url="http://127.0.0.1:11434/v1")
        assert codex._credentials_provider is None

    def test_custom_provider_without_base_url_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, CODEX_BASE_URL=None)
        with pytest.raises(ExtensionSetupError, match="CODEX_BASE_URL"):
            build_worker(_parsed(), _daemon())

    async def test_custom_provider_with_api_key_attaches_static_credentials(self, monkeypatch):
        """An API key makes the endpoint authenticated: the secret rides the
        credentials provider, and no principal is attached - there is nothing
        to attribute usage to on a plain API-key remote."""
        _set_env(
            monkeypatch,
            CODEX_BASE_URL="https://api.openai.com/v1",
            CODEX_API_KEY="sk-test",
        )
        worker = build_worker(_parsed(), _daemon(identity_id=None))

        codex = worker._tools.codex
        assert codex.config.model_access == RemoteAccess(base_url="https://api.openai.com/v1")
        assert codex.config.model_access.principal_header is None
        assert codex._credentials_provider is not None
        creds = await codex._credentials_provider()
        assert creds.secret == "sk-test"
        assert creds.principal is None

    def test_daemon_llm_variable_picks_model_when_directive_omits_it(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon(llm="qwen"))

        assert worker._tools.codex.config.model == "qwen"

    def test_aion_provider_uses_model_service_with_token_resolver(self, monkeypatch):
        from aion.api.control_plane import AION_PRINCIPAL_SELECTOR_HEADER

        _set_env(monkeypatch, CODEX_PROVIDER="aion", CODEX_BASE_URL=None)
        monkeypatch.setattr(
            "aion.api.model_service_client.aion_model_base_url",
            lambda: "https://api.aion.example/v1",
        )
        worker = build_worker(_parsed(), _daemon(llm="qwen"))

        codex = worker._tools.codex
        assert codex.config.model_access == RemoteAccess(
            base_url="https://api.aion.example/v1",
            principal_header=AION_PRINCIPAL_SELECTOR_HEADER,
        )
        assert codex.config.model == "qwen"
        assert codex._credentials_provider is not None

    def test_aion_mode_requires_daemon_identity(self, monkeypatch):
        """Going to the model service without a principal would leave usage
        unattributed - reject before the run starts."""
        _set_env(monkeypatch, CODEX_PROVIDER="aion", CODEX_BASE_URL=None)
        with pytest.raises(ExtensionSetupError, match="daemonAgentIdentityId"):
            build_worker(_parsed(), _daemon(identity_id=None))

    def test_missing_provider_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, CODEX_PROVIDER=None)
        with pytest.raises(ExtensionSetupError, match="CODEX_PROVIDER"):
            build_worker(_parsed(), _daemon())

    def test_unknown_provider_raises_setup_error(self, monkeypatch):
        _set_env(monkeypatch, CODEX_PROVIDER="openai")
        with pytest.raises(ExtensionSetupError, match="CODEX_PROVIDER"):
            build_worker(_parsed(), _daemon())

    def test_irrelevant_key_is_ignored_with_warning(self, monkeypatch, caplog):
        """A leftover variable must not fail the deployment, but silently
        paying with the wrong quota must not be silent either."""
        _set_env(
            monkeypatch,
            CODEX_PROVIDER="aion",
            CODEX_BASE_URL=None,
            CODEX_API_KEY="sk-leftover",
        )
        monkeypatch.setattr(
            "aion.api.model_service_client.aion_model_base_url",
            lambda: "https://api.aion.example/v1",
        )
        with caplog.at_level("WARNING"):
            worker = build_worker(_parsed(), _daemon())
        assert "CODEX_API_KEY" in caplog.text
        assert worker._tools.codex.config.model_access.base_url == "https://api.aion.example/v1"

    def test_unsupported_directive_values_surface_as_setup_error(self, monkeypatch):
        """The core payload allows kind/mode values the installed toolkit may
        not support yet (e.g. kind='bugfix' vs the toolkit's Literal['feature']);
        that mismatch must fail the task with a clear message, not a traceback."""
        from aion.toolkits.behaviour_evolution import Kind

        _set_env(monkeypatch)
        if "bugfix" in getattr(Kind, "__args__", ()):
            pytest.skip("installed toolkit already supports kind='bugfix'")

        with pytest.raises(ExtensionSetupError) as ex:
            build_worker(_parsed(kind="bugfix"), _daemon())

        message = str(ex.value)
        # Names the field, the requested value, and what the toolkit accepts —
        # not a raw pydantic dump.
        assert "behaviour-evolution toolkit does not support" in message
        assert "kind='bugfix'" in message
        assert "feature" in message
        assert "validation error for EvolutionDirective" not in message

    def test_unsupported_mode_surfaces_as_setup_error(self, monkeypatch):
        """Same capability-gap handling for mode: aion.core advertises
        mode='directive', the installed toolkit accepts only 'advisory'."""
        from aion.toolkits.behaviour_evolution import Mode

        _set_env(monkeypatch)
        if "directive" in getattr(Mode, "__args__", ()):
            pytest.skip("installed toolkit already supports mode='directive'")

        with pytest.raises(ExtensionSetupError) as ex:
            build_worker(_parsed(mode="directive"), _daemon())

        message = str(ex.value)
        assert "mode='directive'" in message
        assert "advisory" in message
        assert "validation error for EvolutionDirective" not in message


class TestDirectiveModelPreferences:
    """Per-run model tuning from the directive's `model` field. Priority is
    directive -> `llm` config var -> toolkit default - there is no env
    override any more (see the tools_factory module docstring)."""

    def test_directive_name_used_when_config_var_unset(self, monkeypatch):
        _set_env(monkeypatch)
        daemon = _daemon(llm=None)
        worker = build_worker(_parsed(model=ModelPreferences(name="gpt-5.1-codex")), daemon)
        assert worker._tools.codex.config.model == "gpt-5.1-codex"

    def test_directive_name_overrides_daemon_llm_config_var(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(
            _parsed(model=ModelPreferences(name="gpt-5.1-codex")), _daemon(llm="qwen")
        )
        assert worker._tools.codex.config.model == "gpt-5.1-codex"

    def test_no_directive_model_falls_back_to_daemon_llm_config_var(self, monkeypatch):
        """Regression: directives that omit `model` keep today's behaviour."""
        _set_env(monkeypatch)
        worker = build_worker(_parsed(model=None), _daemon(llm="qwen"))
        assert worker._tools.codex.config.model == "qwen"

    def test_directive_reasoning_effort_used(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(model=ModelPreferences(reasoning_effort="high")), _daemon())
        assert worker._tools.codex.config.model_reasoning_effort == "high"

    def test_directive_context_window_used(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(model=ModelPreferences(context_window=64000)), _daemon())
        assert worker._tools.codex.config.model_context_window == 64000

    def test_directive_model_cannot_touch_trust_boundary_fields(self, monkeypatch):
        """The directive's `model` preferences must never reach model_access,
        model_catalog_json, or codex_bin - those decide whose credentials/quota
        pay and what gets exec'd, which stays a deployment-operator decision."""
        _set_env(monkeypatch, CODEX_MODEL_CATALOG_JSON="/etc/aion/catalog.json")
        worker = build_worker(
            _parsed(
                model=ModelPreferences(
                    name="gpt-5.1-codex", reasoning_effort="high", context_window=64000
                )
            ),
            _daemon(),
        )
        codex_config = worker._tools.codex.config
        assert codex_config.model_access == RemoteAccess(base_url="http://127.0.0.1:11434/v1")
        assert codex_config.model_catalog_json == "/etc/aion/catalog.json"
        assert codex_config.codex_bin == "codex"


class TestDirectiveBranchStrategy:
    """Branch strategy from the directive's own `branch_strategy` field.
    Priority is directive -> daemon config var (kept only for directives that
    predate this field) -> "beta-branch" default - no env override any more."""

    def test_directive_value_used(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(branch_strategy="pull-request"), _daemon())
        assert worker._config.branch_strategy == "pull-request"

    def test_directive_overrides_daemon_config_var(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(
            _parsed(branch_strategy="pull-request"),
            _daemon(branch_strategy="beta-branch"),
        )
        assert worker._config.branch_strategy == "pull-request"

    def test_no_directive_value_falls_back_to_daemon_config_var(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon(branch_strategy="pull-request"))
        assert worker._config.branch_strategy == "pull-request"

    def test_defaults_to_beta_branch(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(), _daemon())
        assert worker._config.branch_strategy == "beta-branch"

    def test_unknown_daemon_config_var_value_raises_setup_error(self, monkeypatch):
        """The directive's own `branch_strategy` is a pydantic Literal, so an
        unknown value never reaches this deployment at all; the daemon config
        var fallback has no such type check, so it still needs one here."""
        _set_env(monkeypatch)
        with pytest.raises(ExtensionSetupError, match="unsupported evolution branch strategy"):
            build_worker(_parsed(), _daemon(branch_strategy="direct-push"))


class TestDirectiveRunLimits:
    """Resource ceilings from the directive's `limits` field. Unlike `model`,
    there is no deployment-side value to fall back to or clamp against - the
    caller sets these purely to protect its own run."""

    def test_max_total_tokens_reaches_codex_config(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(limits=RunLimits(max_total_tokens=120000)), _daemon())
        assert worker._tools.codex.config.max_total_tokens == 120000

    def test_timeouts_reach_evolution_config(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(
            _parsed(
                limits=RunLimits(
                    op_timeout=30.0, network_timeout=45.5, codex_timeout=600.0
                )
            ),
            _daemon(),
        )
        config = worker._config
        assert config.op_timeout == 30.0
        assert config.network_timeout == 45.5
        assert config.codex_timeout == 600.0
        # build_tools wires codex_timeout (falling back to op_timeout) as the
        # CodexClient wall-clock ceiling.
        assert worker._tools.codex._timeout == 600.0

    def test_codex_timeout_falls_back_to_op_timeout(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(limits=RunLimits(op_timeout=90.0)), _daemon())
        assert worker._tools.codex._timeout == 90.0

    def test_no_directive_limits_leaves_everything_unbounded(self, monkeypatch):
        _set_env(monkeypatch)
        worker = build_worker(_parsed(limits=None), _daemon())
        assert worker._config.op_timeout is None
        assert worker._tools.codex.config.max_total_tokens is None
