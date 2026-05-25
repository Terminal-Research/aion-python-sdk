import logging
from types import SimpleNamespace

import pytest

from aion.api import aion_openai_config
from aion.api.exceptions import AionAuthenticationError
from aion.api.http.jwt_manager import AionRefreshingJWTManager
import aion.api.model_service_client as model_service_client


class FakeSyncJWTManager:
    def __init__(self, token="jwt-token"):
        self.token = token
        self.calls = 0

    def get_token_sync(self):
        self.calls += 1
        return self.token


def test_openai_config_is_available_from_package_and_module():
    assert aion_openai_config is model_service_client.aion_openai_config


def test_builds_model_base_url(monkeypatch):
    monkeypatch.setattr(
        model_service_client,
        "api_settings",
        SimpleNamespace(http_url="https://api.example.test"),
    )

    assert model_service_client.aion_model_base_url() == (
        "https://api.example.test/v1"
    )


def test_model_api_key_uses_configured_jwt_manager():
    jwt_manager = FakeSyncJWTManager("jwt-token")

    assert model_service_client.aion_model_api_key(jwt_manager) == "jwt-token"
    assert jwt_manager.calls == 1


def test_model_api_key_provider_refreshes_on_each_call():
    jwt_manager = FakeSyncJWTManager("jwt-token")
    provider = model_service_client.aion_model_api_key_provider(jwt_manager)

    assert provider() == "jwt-token"
    assert provider() == "jwt-token"
    assert jwt_manager.calls == 2


def test_refreshing_jwt_manager_supports_sync_token_provider(
    valid_jwt_token,
):
    class FakeHttpClient:
        def __init__(self):
            self.calls = 0

        def authenticate_sync(self):
            self.calls += 1
            return {"accessToken": valid_jwt_token}

    jwt_manager = AionRefreshingJWTManager()
    jwt_manager._client = FakeHttpClient()

    assert jwt_manager.get_token_sync() == valid_jwt_token
    assert jwt_manager.get_token_sync() == valid_jwt_token
    assert jwt_manager._client.calls == 1


def test_model_api_key_raises_when_jwt_cannot_be_resolved():
    jwt_manager = FakeSyncJWTManager(None)

    with pytest.raises(AionAuthenticationError):
        model_service_client.aion_model_api_key(jwt_manager)


def test_builds_openai_compatible_kwargs(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="aion.api.model_service_client")
    monkeypatch.setattr(
        model_service_client,
        "api_settings",
        SimpleNamespace(
            client_id="client-id",
            client_secret="secret",
            http_url="https://api.example.test",
        ),
    )

    config = model_service_client.aion_openai_config()
    api_key = config.api_key
    assert callable(api_key)

    assert config.openai_kwargs() == {
        "base_url": "https://api.example.test/v1",
        "api_key": api_key,
    }
    assert config.litellm_kwargs() == {
        "api_base": "https://api.example.test/v1",
        "api_key": api_key,
    }
    assert "without principal attribution" in caplog.text


def test_model_request_headers_adds_principal_from_provider():
    headers = model_service_client.aion_model_request_headers(
        principal_selector_provider=lambda: "agent-environment:env-id"
    )

    assert headers == {
        model_service_client.AION_PRINCIPAL_SELECTOR_HEADER: (
            "agent-environment:env-id"
        )
    }


def test_model_request_headers_does_not_mutate_existing_headers():
    existing = {"X-Request-ID": "request-1"}

    headers = model_service_client.aion_model_request_headers(
        existing,
        principal_selector_provider=lambda: "agent-identity:identity-id",
    )

    assert existing == {"X-Request-ID": "request-1"}
    assert headers == {
        "X-Request-ID": "request-1",
        model_service_client.AION_PRINCIPAL_SELECTOR_HEADER: (
            "agent-identity:identity-id"
        ),
    }


def test_model_request_headers_preserves_existing_principal(caplog):
    caplog.set_level(logging.WARNING, logger="aion.api.model_service_client")
    existing = {
        model_service_client.AION_PRINCIPAL_SELECTOR_HEADER: (
            "agent-environment:env-id"
        )
    }

    headers = model_service_client.aion_model_request_headers(existing)

    assert headers == {
        model_service_client.AION_PRINCIPAL_SELECTOR_HEADER: (
            "agent-environment:env-id"
        )
    }
    assert "without principal attribution" not in caplog.text


def test_client_config_returns_fresh_request_headers(monkeypatch):
    monkeypatch.setattr(
        model_service_client,
        "api_settings",
        SimpleNamespace(
            client_id="client-id",
            client_secret="secret",
            http_url="https://api.example.test",
        ),
    )
    config = model_service_client.aion_openai_config()

    headers = config.request_headers(
        {"X-Request-ID": "request-1"},
        warn_on_missing=False,
    )

    assert headers == {"X-Request-ID": "request-1"}
    assert headers is not config.default_headers


def test_principal_selector_headers_are_optional(caplog):
    caplog.set_level(logging.WARNING, logger="aion.api.model_service_client")

    assert model_service_client.aion_principal_selector_headers() == {}
    assert "without principal attribution" in caplog.text


def test_principal_selector_returns_none_when_no_provider(monkeypatch):
    """Verify selector returns None when no runtime context provider is registered."""
    from aion.core.runtime.context.registry import AionRuntimeContextRegistry
    monkeypatch.setattr(AionRuntimeContextRegistry, "_provider", None)

    assert model_service_client.aion_principal_selector() is None


def test_principal_selector_returns_none_when_provider_returns_none(monkeypatch):
    """Verify selector returns None when provider returns None (out of scope)."""
    from aion.core.runtime.context.registry import AionRuntimeContextRegistry
    provider = SimpleNamespace(get_current_context=lambda: None)
    monkeypatch.setattr(AionRuntimeContextRegistry, "_provider", provider)

    assert model_service_client.aion_principal_selector() is None


def test_principal_selector_reads_environment_from_context(monkeypatch):
    """Verify selector returns agent-environment selector from context."""
    from aion.core.runtime.context.registry import AionRuntimeContextRegistry
    ctx = SimpleNamespace(get_principal_selector=lambda: "agent-environment:env-42")
    provider = SimpleNamespace(get_current_context=lambda: ctx)
    monkeypatch.setattr(AionRuntimeContextRegistry, "_provider", provider)

    assert model_service_client.aion_principal_selector() == "agent-environment:env-42"


def test_principal_selector_reads_daemon_identity_from_context(monkeypatch):
    """Verify selector prefers agent-identity when daemon identity is present."""
    from aion.core.runtime.context.registry import AionRuntimeContextRegistry
    ctx = SimpleNamespace(get_principal_selector=lambda: "agent-identity:daemon-99")
    provider = SimpleNamespace(get_current_context=lambda: ctx)
    monkeypatch.setattr(AionRuntimeContextRegistry, "_provider", provider)

    assert model_service_client.aion_principal_selector() == "agent-identity:daemon-99"
