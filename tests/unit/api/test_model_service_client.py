import logging
from types import SimpleNamespace

import httpx
import pytest

from aion.api import aion_openai_config
from aion.api.exceptions import AionAuthenticationError, AionModelPrincipalError
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
    configured_auth,
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
        "default_headers": {
            "Accept": "application/json, text/event-stream",
        },
    }
    assert config.litellm_kwargs() == {
        "api_base": "https://api.example.test/v1",
        "api_key": api_key,
    }
    assert "without principal attribution" not in caplog.text


def test_static_kwargs_do_not_resolve_principal(monkeypatch):
    config = model_service_client.AionModelClientConfig(
        base_url="https://api.example.test/v1",
        api_key="jwt-token",
    )

    def fail_if_called():
        pytest.fail("principal selector should not be resolved for static kwargs")

    monkeypatch.setattr(
        model_service_client, "aion_principal_selector", fail_if_called
    )

    openai_kwargs = config.openai_kwargs()
    litellm_kwargs = config.litellm_kwargs()

    assert openai_kwargs["default_headers"] == {
        "Accept": "application/json, text/event-stream",
    }
    assert "extra_headers" not in litellm_kwargs


def test_langchain_kwargs_add_request_scoped_clients(monkeypatch):
    api_key_provider = lambda: "fresh-jwt"
    config = model_service_client.AionModelClientConfig(
        base_url="https://api.example.test/v1",
        api_key=api_key_provider,
    )
    captured_providers = []

    def sync_client(**kwargs):
        captured_providers.append(kwargs["api_key_provider"])
        return "sync-client"

    def async_client(**kwargs):
        captured_providers.append(kwargs["api_key_provider"])
        return "async-client"

    monkeypatch.setattr(
        model_service_client,
        "_aion_model_http_client",
        lambda api_key_provider: sync_client(api_key_provider=api_key_provider),
    )
    monkeypatch.setattr(
        model_service_client,
        "_aion_model_async_http_client",
        lambda api_key_provider: async_client(
            api_key_provider=api_key_provider
        ),
    )

    kwargs = config.langchain_openai_kwargs()

    assert kwargs["api_key"] == "aion-runtime-token"
    assert kwargs["http_client"] == "sync-client"
    assert kwargs["http_async_client"] == "async-client"
    assert captured_providers == [api_key_provider, api_key_provider]


@pytest.mark.parametrize("selector", [None, "aion://agent/environment/env", "invalid"])
def test_model_rejects_retired_selector_provider(selector):
    with pytest.raises(AionAuthenticationError, match="no longer supported"):
        model_service_client.aion_model_request_headers(
            principal_selector_provider=lambda: selector,
            usage_attribution_provider=lambda: "opaque",
        )


@pytest.mark.parametrize("key", ["Aion-Principal-Selector", "aion-principal-selector"])
def test_model_rejects_retired_header_even_with_carrier(key):
    with pytest.raises(AionAuthenticationError, match="no longer supported"):
        model_service_client.aion_model_request_headers(
            {key: "aion://agent/identity/old", "Aion-Usage-Attribution": "opaque"}
        )


def test_model_requires_attribution_not_selector():
    with pytest.raises(AionAuthenticationError, match="request-local attribution"):
        model_service_client.aion_model_request_headers()


@pytest.mark.parametrize("key", ["Aion-Usage-Attribution", "aion-usage-attribution"])
def test_model_forwards_opaque_carrier_without_selector(key):
    existing = {key: "opaque", "X-Request-ID": "request-1"}
    assert model_service_client.aion_model_request_headers(existing) == {
        "Aion-Usage-Attribution": "opaque", "X-Request-ID": "request-1",
    }
    assert existing == {key: "opaque", "X-Request-ID": "request-1"}


def test_model_explicit_carrier_cannot_override_provider():
    with pytest.raises(AionAuthenticationError, match="Conflicting"):
        model_service_client.aion_model_request_headers(
            {"Aion-Usage-Attribution": "other"},
            usage_attribution_provider=lambda: "current",
        )


def test_model_http_client_refreshes_credentials_and_scope_per_request(monkeypatch):
    from aion.core.runtime.context import AionRuntimeContext, ForwardedAttribution
    contexts = iter(
        AionRuntimeContext(callback_attribution=ForwardedAttribution(value))
        for value in ("first", "second")
    )
    monkeypatch.setattr(
        "aion.api.callback_attribution.get_aion_runtime_context", lambda: next(contexts)
    )
    tokens = iter(["version-1", "version-2"])
    with model_service_client._aion_model_http_client(lambda: next(tokens)) as client:
        requests = [
            client.build_request("POST", "https://api.example.test/v1/chat/completions")
            for _ in range(2)
        ]
        for request in requests:
            for hook in client.event_hooks["request"]:
                hook(request)
    for request, token, carrier in zip(requests, ("version-1", "version-2"), ("first", "second")):
        assert request.headers["Authorization"] == f"Bearer {token}"
        assert request.headers["Aion-Usage-Attribution"] == carrier
        assert "Aion-Principal-Selector" not in request.headers


def test_graphql_model_principal_normalization_remains_lenient(caplog):
    caplog.set_level(logging.ERROR, logger="aion.api.model_service_client")
    assert model_service_client.aion_model_principal_selector_value(
        "aion://agent/environment/env-id"
    ) is None
    assert "environment selector will not be sent" in caplog.text


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
    ctx = SimpleNamespace(
        get_principal_selector=lambda: "aion://agent/environment/env-42"
    )
    provider = SimpleNamespace(get_current_context=lambda: ctx)
    monkeypatch.setattr(AionRuntimeContextRegistry, "_provider", provider)

    assert (
        model_service_client.aion_principal_selector()
        == "aion://agent/environment/env-42"
    )


def test_principal_selector_reads_daemon_identity_from_context(monkeypatch):
    """Verify selector prefers agent-identity when daemon identity is present."""
    from aion.core.runtime.context.registry import AionRuntimeContextRegistry
    ctx = SimpleNamespace(
        get_principal_selector=lambda: "aion://agent/identity/daemon-99"
    )
    provider = SimpleNamespace(get_current_context=lambda: ctx)
    monkeypatch.setattr(AionRuntimeContextRegistry, "_provider", provider)

    assert (
        model_service_client.aion_principal_selector()
        == "aion://agent/identity/daemon-99"
    )
