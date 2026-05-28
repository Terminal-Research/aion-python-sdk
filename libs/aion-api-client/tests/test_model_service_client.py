import logging
from types import SimpleNamespace

import httpx
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


def test_model_request_hook_resolves_principal_at_request_time(monkeypatch):
    selectors = iter([
        "agent-environment:first-env",
        "agent-environment:second-env",
    ])
    monkeypatch.setattr(
        model_service_client, "aion_principal_selector", lambda: next(selectors)
    )
    first_request = httpx.Request(
        "POST", "https://api.example.test/v1/chat/completions"
    )
    second_request = httpx.Request(
        "POST", "https://api.example.test/v1/chat/completions"
    )

    model_service_client.aion_model_request_hook(first_request)
    model_service_client.aion_model_request_hook(second_request)

    assert (
        first_request.headers[model_service_client.AION_PRINCIPAL_SELECTOR_HEADER]
        == "agent-environment:first-env"
    )
    assert (
        second_request.headers[model_service_client.AION_PRINCIPAL_SELECTOR_HEADER]
        == "agent-environment:second-env"
    )


def test_model_request_hook_preserves_explicit_principal(monkeypatch):
    monkeypatch.setattr(
        model_service_client,
        "aion_principal_selector",
        lambda: "agent-environment:fresh-env",
    )
    request = httpx.Request(
        "POST",
        "https://api.example.test/v1/chat/completions",
        headers={
            model_service_client.AION_PRINCIPAL_SELECTOR_HEADER: (
                "agent-environment:explicit-env"
            )
        },
    )

    model_service_client.aion_model_request_hook(request)

    assert (
        request.headers[model_service_client.AION_PRINCIPAL_SELECTOR_HEADER]
        == "agent-environment:explicit-env"
    )


def test_model_http_client_refreshes_api_key_per_request(monkeypatch):
    tokens = iter(["jwt-1", "jwt-2"])
    monkeypatch.setattr(
        model_service_client,
        "aion_principal_selector",
        lambda: "agent-environment:fresh-env",
    )
    client = model_service_client._aion_model_http_client(
        lambda: next(tokens)
    )
    first_request = client.build_request(
        "POST", "https://api.example.test/v1/chat/completions"
    )
    second_request = client.build_request(
        "POST", "https://api.example.test/v1/chat/completions"
    )
    try:
        for hook in client.event_hooks["request"]:
            hook(first_request)
        for hook in client.event_hooks["request"]:
            hook(second_request)
    finally:
        client.close()

    assert first_request.headers["Authorization"] == "Bearer jwt-1"
    assert second_request.headers["Authorization"] == "Bearer jwt-2"


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
