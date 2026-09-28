"""Tests for the SDK-wide exception hierarchy rooted at ``AionError``."""

import importlib
from pathlib import Path

import pytest

import aion.core.exceptions as core_exceptions
from aion.core.exceptions import (
    AionAuthenticationError,
    AionError,
    AionModelPrincipalError,
    ConfigurationError,
    MissingOptionalDependency,
)

# Every internal root, named by the module that owns it. Their raise sites stay
# where they are; only the base is shared, so `except AionError` around SDK
# code catches all of them.
INTERNAL_ROOTS = [
    ("aion.server.agent.exceptions", "AdapterError"),
    ("aion.proxy.exceptions", "AgentProxyException"),
    ("aion.server.server", "MissingAPIKeyError"),
    ("aion.server.agent.execution.extensions.errors", "ExtensionPreflightError"),
    ("aion.server.agent.execution.extensions.evolution.errors", "EvolutionHandlerError"),
    ("aion.server.tasks.ownership.types", "TaskOwnershipLost"),
    ("aion.core.runtime.context.extensions.descriptors", "ExtensionActivationError"),
    ("aion.cli.services.chat.credentials", "CredentialHelperError"),
    ("aion.cli.services.chat.launcher", "BinaryResolutionError"),
]


def _load(module_name: str, class_name: str) -> type[BaseException]:
    """Import ``class_name`` from ``module_name``, skipping if an extra is absent."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - only on a partial install
        pytest.skip(f"{module_name} needs an extra that is not installed: {exc}")
    return getattr(module, class_name)


class TestPublicHierarchy:
    @pytest.mark.parametrize(
        "name", [n for n in core_exceptions.__all__ if n != "AionError"]
    )
    def test_public_exception_inherits_root(self, name):
        """Every name aion.core.exceptions exports descends from AionError."""
        cls = getattr(core_exceptions, name)
        assert issubclass(cls, AionError), f"{name} must inherit AionError"

    def test_root_is_an_exception(self):
        """AionError is an ordinary Exception, so a bare `except Exception` still sees it."""
        assert issubclass(AionError, Exception)

    def test_missing_optional_dependency_is_import_error(self):
        """MissingOptionalDependency keeps ImportError as a base for `except ImportError` callers."""
        assert issubclass(MissingOptionalDependency, ImportError)

    def test_missing_optional_dependency_keeps_name_keyword(self):
        """The ImportError `name` keyword still reaches ImportError.__init__ through the MRO."""
        error = MissingOptionalDependency("message", name="sqlalchemy")
        assert error.name == "sqlalchemy"
        assert str(error) == "message"

    def test_configuration_error_formats_file_path_and_details(self):
        """ConfigurationError still builds its combined message from all three arguments."""
        error = ConfigurationError("bad value", details="line 3", file_path=Path("aion.yaml"))
        assert "aion.yaml" in str(error)
        assert "bad value" in str(error)
        assert "line 3" in str(error)

    def test_authentication_error_keeps_status_code(self):
        """AionAuthenticationError still carries the HTTP status it was built with."""
        assert AionAuthenticationError("denied", 401).status_code == 401

    def test_model_principal_error_keeps_selector(self):
        """AionModelPrincipalError is an authentication error carrying its selector."""
        error = AionModelPrincipalError("no principal", selector="daemon")
        assert error.selector == "daemon"
        assert isinstance(error, AionAuthenticationError)


class TestReExports:
    """The pre-existing import paths must name the same classes, not copies."""

    def test_api_exceptions_module(self):
        """aion.api.exceptions re-exports the auth classes rather than redefining them."""
        module = importlib.import_module("aion.api.exceptions")
        assert module.AionAuthenticationError is AionAuthenticationError
        assert module.AionModelPrincipalError is AionModelPrincipalError

    def test_config_package_and_module(self):
        """Both aion.core.config paths for ConfigurationError resolve to the core class."""
        assert importlib.import_module("aion.core.config").ConfigurationError is ConfigurationError
        assert (
            importlib.import_module("aion.core.config.exceptions").ConfigurationError
            is ConfigurationError
        )

    def test_optional_deps_module(self):
        """aion.core.utils.optional_deps still exposes the exception its factories build."""
        module = importlib.import_module("aion.core.utils.optional_deps")
        assert module.MissingOptionalDependency is MissingOptionalDependency
        assert isinstance(module.missing_extra_error("f", "server"), MissingOptionalDependency)


class TestInternalRoots:
    @pytest.mark.parametrize("module_name,class_name", INTERNAL_ROOTS)
    def test_internal_root_inherits_aion_error(self, module_name, class_name):
        """Server, proxy and CLI exception roots all descend from AionError."""
        cls = _load(module_name, class_name)
        assert issubclass(cls, AionError), f"{module_name}.{class_name} must inherit AionError"

    @pytest.mark.parametrize("module_name,class_name", [
        ("aion.server.tasks.ownership.types", "TaskOwnershipLost"),
        ("aion.cli.services.chat.credentials", "CredentialHelperError"),
        ("aion.cli.services.chat.launcher", "BinaryResolutionError"),
    ])
    def test_runtime_error_base_is_kept(self, module_name, class_name):
        """Classes that were RuntimeErrors stay catchable as RuntimeError."""
        assert issubclass(_load(module_name, class_name), RuntimeError)

    def test_proxy_exceptions_stay_http_exceptions(self):
        """Proxy errors keep HTTPException, which is how FastAPI maps them to a status."""
        from fastapi import HTTPException

        module = importlib.import_module("aion.proxy.exceptions")
        for name in (
            "AgentNotFoundException",
            "AgentUnavailableException",
            "AgentTimeoutException",
            "AgentProxyException",
        ):
            assert issubclass(getattr(module, name), HTTPException)

    def test_a2a_protocol_errors_stay_out_of_the_hierarchy(self):
        """TaskOwnershipBusy belongs to a2a-sdk's vocabulary, not to AionError."""
        from a2a.utils.errors import A2AError

        busy = importlib.import_module("aion.server.tasks.ownership.types").TaskOwnershipBusy
        assert issubclass(busy, A2AError)
        assert not issubclass(busy, AionError)


def test_dead_server_exceptions_package_is_gone():
    """aion.server.exceptions was an unimported duplicate of the auth classes."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aion.server.exceptions")
