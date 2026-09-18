"""The evolution extension's environment is read and parsed in one place.

These run everywhere, which is the point of them. The tests for the factory
that consumes these values are skipped unless the private behaviour-evolution
toolkit is installed - no extra of this package pulls it in - so the parsing
they used to cover ran unverified on every ordinary suite. Parsing needs no
toolkit, so it is held here.
"""

from __future__ import annotations

import os

import pytest

from aion.server.agent.execution.extensions.evolution.errors import ExtensionSetupError
from aion.server.agent.execution.extensions.evolution.settings import EvolutionSettings


VARIABLES = (
    "CODEX_API_KEY", "CODEX_BASE_URL", "CODEX_BIN", "CODEX_HOME",
    "CODEX_MODEL_CATALOG_JSON", "CODEX_PROVIDER",
    "EVOLUTION_EXECUTOR_NETWORK", "EVOLUTION_SETUP_COMMAND",
    "EVOLUTION_SETUP_TIMEOUT", "EVOLUTION_SPECS_ROOT",
    "EVOLUTION_WORKDIR_ROOT", "GITHUB_TOKEN",
)


@pytest.fixture(autouse=True)
def _restore_environment():
    """Put the process environment back after each test.

    The environment stays applied for the body of a test rather than only for
    the construction call, because two of these values are properties that read
    it again when they are asked. A helper that restored on the way out would
    hand back an object whose credentials had already gone.
    """
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def _settings(**environment: str) -> EvolutionSettings:
    """Read the settings against exactly this environment, and leave it in place."""
    for key in VARIABLES:
        os.environ.pop(key, None)
    os.environ.update(environment)
    return EvolutionSettings()


class TestAbsence:
    """Nothing is required here: what a run needs depends on what it selected."""

    def test_an_empty_environment_reads_without_failing(self) -> None:
        """A deployment that never activates evolution never fails on its config."""
        settings = _settings()

        assert settings.provider is None
        assert settings.github_token is None
        assert settings.specs_root is None
        assert settings.setup_command is None
        assert settings.setup_timeout is None

    def test_the_codex_executable_defaults_to_codex(self) -> None:
        """The one field with a default keeps it."""
        assert _settings().codex_bin == "codex"

    def test_network_access_is_off_by_default(self) -> None:
        """The sandbox grant is opt-in."""
        assert _settings().executor_network is False


class TestProvider:
    """The name is normalized here; whether it is a known one is decided elsewhere."""

    @pytest.mark.parametrize("raw", ["custom", "CUSTOM", "  Custom  "])
    def test_case_and_padding_do_not_change_the_provider(self, raw: str) -> None:
        assert _settings(CODEX_PROVIDER=raw).provider == "custom"

    def test_a_blank_provider_is_absence_not_an_empty_name(self) -> None:
        """`CODEX_PROVIDER=` must reach the check that refuses to guess."""
        assert _settings(CODEX_PROVIDER="   ").provider is None

    def test_an_unknown_provider_is_carried_through_unjudged(self) -> None:
        """Rejecting it here would take the message away from the code that lists the alternatives."""
        assert _settings(CODEX_PROVIDER="nonesuch").provider == "nonesuch"


class TestNetworkFlag:
    """A sandbox grant, so a typo must neither enable it nor fail the run."""

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_the_documented_spellings_grant_network(self, raw: str) -> None:
        assert _settings(EVOLUTION_EXECUTOR_NETWORK=raw).executor_network is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
    def test_the_documented_negatives_withhold_it(self, raw: str) -> None:
        assert _settings(EVOLUTION_EXECUTOR_NETWORK=raw).executor_network is False

    def test_an_unrecognized_value_withholds_it_and_says_so(self, caplog) -> None:
        """Silence here would be a grant nobody asked for, or a refusal nobody sees."""
        with caplog.at_level("WARNING"):
            settings = _settings(EVOLUTION_EXECUTOR_NETWORK="ja")

        assert settings.executor_network is False
        assert "EVOLUTION_EXECUTOR_NETWORK" in caplog.text


class TestSetupCommand:
    """A command line written by an operator, split the way a shell would."""

    def test_a_quoted_line_becomes_argv(self) -> None:
        settings = _settings(
            EVOLUTION_SETUP_COMMAND='.venv/bin/pip install -e . --no-deps'
        )

        assert settings.setup_command == [
            ".venv/bin/pip", "install", "-e", ".", "--no-deps"
        ]

    def test_quoting_holds_an_argument_together(self) -> None:
        settings = _settings(EVOLUTION_SETUP_COMMAND='make test ARGS="-k slow"')

        assert settings.setup_command == ["make", "test", 'ARGS=-k slow']

    def test_a_blank_line_is_no_command(self) -> None:
        assert _settings(EVOLUTION_SETUP_COMMAND="   ").setup_command is None

    def test_an_unbalanced_quote_fails_naming_the_variable(self) -> None:
        """Otherwise this surfaces as a bare ValueError from inside shlex."""
        with pytest.raises(ExtensionSetupError) as failure:
            _settings(EVOLUTION_SETUP_COMMAND='pip install "unclosed')

        assert "EVOLUTION_SETUP_COMMAND" in str(failure.value)


class TestSetupTimeout:
    """Seconds, or a failure that names the variable rather than the parser."""

    @pytest.mark.parametrize(("raw", "expected"), [("90", 90.0), ("0.5", 0.5)])
    def test_a_number_is_read_as_seconds(self, raw: str, expected: float) -> None:
        assert _settings(EVOLUTION_SETUP_TIMEOUT=raw).setup_timeout == expected

    def test_a_blank_value_is_no_timeout(self) -> None:
        assert _settings(EVOLUTION_SETUP_TIMEOUT=" ").setup_timeout is None

    def test_a_non_number_fails_naming_the_variable(self) -> None:
        with pytest.raises(ExtensionSetupError) as failure:
            _settings(EVOLUTION_SETUP_TIMEOUT="soon")

        assert "EVOLUTION_SETUP_TIMEOUT" in str(failure.value)
        assert "soon" in str(failure.value)


class TestCredentialsAreNotFields:
    """The two secrets here are properties, and both reasons matter.

    A deployment may replace a credential while the pod runs, and an evolution
    run clones, commits and pushes over tens of minutes - so a value captured
    when the run started can be one the forge has already stopped accepting.
    And a pydantic field holding a secret is a secret in `repr()` and
    `model_dump()`, which is one log line away from being written down.
    """

    def test_a_replaced_forge_token_is_seen_by_a_run_already_under_way(self) -> None:
        settings = _settings(GITHUB_TOKEN="ghp_at_setup")
        assert settings.github_token == "ghp_at_setup"

        os.environ["GITHUB_TOKEN"] = "ghp_rotated"
        assert settings.github_token == "ghp_rotated"

    def test_a_replaced_model_key_is_seen_the_same_way(self) -> None:
        settings = _settings(CODEX_API_KEY="sk-at-setup")
        assert settings.codex_api_key == "sk-at-setup"

        os.environ["CODEX_API_KEY"] = "sk-rotated"
        assert settings.codex_api_key == "sk-rotated"

    @pytest.mark.parametrize("variable", ["GITHUB_TOKEN", "CODEX_API_KEY"])
    def test_a_blank_credential_reads_as_absent(self, variable: str) -> None:
        """So a caller sees "no credential" rather than sending an empty one."""
        settings = _settings(**{variable: "   "})

        attribute = "github_token" if variable == "GITHUB_TOKEN" else "codex_api_key"
        assert getattr(settings, attribute) is None

    def test_no_secret_appears_in_the_object_representation(self) -> None:
        """A traceback or a debug log carrying this object carries no credential."""
        settings = _settings(GITHUB_TOKEN="ghp_secret", CODEX_API_KEY="sk-secret")

        assert "ghp_secret" not in repr(settings)
        assert "sk-secret" not in repr(settings)

    def test_no_secret_appears_in_a_model_dump(self) -> None:
        """The same for anything that serializes the settings."""
        settings = _settings(GITHUB_TOKEN="ghp_secret", CODEX_API_KEY="sk-secret")
        dumped = str(settings.model_dump())

        assert "ghp_secret" not in dumped
        assert "sk-secret" not in dumped


class TestEverythingElseIsDecidedOncePerRun:
    """A run is configured one way for its whole length, on purpose.

    Re-reading these mid-run would not reconfigure anything - the sandbox and
    the checkout already exist - it would only let this code disagree with
    them. The granularity that does mean something is per request, and it comes
    from constructing this object when a request arrives.
    """

    @pytest.mark.parametrize(
        ("variable", "attribute"),
        [
            ("EVOLUTION_EXECUTOR_NETWORK", "executor_network"),
            ("EVOLUTION_WORKDIR_ROOT", "workdir_root"),
            ("CODEX_BASE_URL", "codex_base_url"),
            ("CODEX_PROVIDER", "provider"),
        ],
    )
    def test_a_later_change_does_not_reach_a_run_already_configured(
        self, variable: str, attribute: str
    ) -> None:
        settings = _settings(**{variable: "on" if "NETWORK" in variable else "first"})
        before = getattr(settings, attribute)

        os.environ[variable] = "second"
        assert getattr(settings, attribute) == before

    def test_the_next_run_does_read_the_change(self) -> None:
        """Per request, which is where reconfiguration can take effect."""
        assert _settings(EVOLUTION_WORKDIR_ROOT="/first").workdir_root == "/first"
        assert _settings(EVOLUTION_WORKDIR_ROOT="/second").workdir_root == "/second"


class TestEveryVariableIsReachable:
    """Each name is readable through the model, which is what replaces the scattered reads."""

    @pytest.mark.parametrize(
        ("variable", "attribute", "value"),
        [
            ("CODEX_API_KEY", "codex_api_key", "sk-test"),
            ("CODEX_BASE_URL", "codex_base_url", "https://models.example"),
            ("CODEX_BIN", "codex_bin", "/usr/local/bin/codex"),
            ("CODEX_HOME", "codex_home", "/var/lib/codex"),
            ("CODEX_MODEL_CATALOG_JSON", "codex_model_catalog_json", "{}"),
            ("EVOLUTION_SPECS_ROOT", "specs_root", "/srv/specs"),
            ("EVOLUTION_WORKDIR_ROOT", "workdir_root", "/srv/work"),
            ("GITHUB_TOKEN", "github_token", "ghp_test"),
        ],
    )
    def test_the_variable_reaches_its_field(
        self, variable: str, attribute: str, value: str
    ) -> None:
        assert getattr(_settings(**{variable: value}), attribute) == value
