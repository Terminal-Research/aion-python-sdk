"""Reject callback ambiguity before it can change the selected authority."""

import pytest

from aion.api.callback_attribution import callback_headers
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context import AionRuntimeContext, ForwardedAttribution


@pytest.mark.parametrize("pairs", [
    [("Aion-Usage-Attribution", "one"), ("aion-usage-attribution", "one")],
    [("Aion-Usage-Attribution", "one"), ("Aion-Caller-Id", "caller")],
    [("Aion-Usage-Attribution", "")],
    [("Aion-Principal-Selector", "")],
    [("Aion-Caller-Id", "invented")],
])
def test_ambiguous_or_obsolete_inputs_never_downgrade(pairs):
    with pytest.raises(AionAuthenticationError):
        callback_headers(pairs)


def test_explicit_carrier_cannot_replace_current_invocation():
    context = AionRuntimeContext(callback_attribution=ForwardedAttribution("current"))
    with pytest.raises(AionAuthenticationError, match="current request"):
        callback_headers(usage_attribution="other", context=context)


def test_identical_explicit_carrier_remains_opaque():
    context = AionRuntimeContext(callback_attribution=ForwardedAttribution("opaque"))
    assert callback_headers(usage_attribution="opaque", context=context) == {
        "Aion-Usage-Attribution": "opaque",
    }


def test_self_initiated_work_preserves_bearer_without_inventing_a_caller(monkeypatch):
    monkeypatch.setattr("aion.api.callback_attribution.get_aion_runtime_context", lambda: None)
    headers = {"Authorization": "Bearer version", "X-Request-ID": "request-1"}
    assert callback_headers(headers) == headers


@pytest.mark.parametrize("builder", ["mcp", "file", "model", "a2a"])
def test_active_request_cannot_silently_become_self_initiated(monkeypatch, builder):
    from aion.api.file_service_client import aion_file_authorization_headers
    from aion.api.gql.client import _with_callback_attribution
    from aion.api.model_service_client import aion_model_request_headers
    from aion.mcp import aion_mcp_authorization_headers

    monkeypatch.setattr("aion.api.callback_attribution.get_aion_runtime_context",
                        lambda: AionRuntimeContext())
    calls = {
        "mcp": lambda: aion_mcp_authorization_headers("version"),
        "file": lambda: aion_file_authorization_headers("version"),
        "model": aion_model_request_headers,
        "a2a": lambda: _with_callback_attribution(None),
    }
    with pytest.raises(AionAuthenticationError, match="request-local attribution"):
        calls[builder]()


@pytest.mark.parametrize("builder", ["mcp", "file", "model"])
def test_each_callback_rejects_stale_explicit_selector(builder):
    from aion.api.file_service_client import aion_file_authorization_headers
    from aion.api.model_service_client import aion_model_request_headers
    from aion.mcp import aion_mcp_authorization_headers
    calls = {
        "mcp": lambda: aion_mcp_authorization_headers("version", principal_selector="old"),
        "file": lambda: aion_file_authorization_headers("version", principal_selector="old"),
        "model": lambda: aion_model_request_headers(principal_selector_provider=lambda: "old"),
    }
    with pytest.raises(AionAuthenticationError, match="no longer supported"):
        calls[builder]()
