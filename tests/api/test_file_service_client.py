from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from aion.api.control_plane import (
    AION_PRINCIPAL_SELECTOR_HEADER,
    PrincipalSelector,
)
from aion.api.file_service_client import AionFileClient
from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER
from aion.core.exceptions import AionError, AionFileValidationError


class StaticTokenManager:
    """Token manager that returns one stable test credential."""

    async def get_token(self) -> str:
        """Return the stable bearer token."""
        return "version-token"


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("association", [
    {"association_kind": "task"},
    {"association_id": "task-1"},
])
async def test_incomplete_association_is_rejected_before_upload(association):
    """Invalid arguments use the shared SDK hierarchy and never reach HTTP."""
    async def unexpected_request(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid File arguments must not be uploaded")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as http_client:
        async with AionFileClient(
            jwt_manager=StaticTokenManager(),
            base_url="https://api.aion.test",
            http_client=http_client,
        ) as client:
            with pytest.raises(AionFileValidationError) as error:
                await client.create(
                    b"media",
                    organization_id="organization-1",
                    purpose="MessagingMedia",
                    file_name="message.bin",
                    **association,
                )

    assert isinstance(error.value, AionError)
    assert isinstance(error.value, ValueError)


@pytest.mark.anyio("asyncio")
async def test_create_forwards_runtime_selector_and_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested File create should forward both request-scoped headers."""
    agent_identity_id = uuid4()
    runtime_context = SimpleNamespace(
        get_principal_selector=lambda: (
            f"aion://agent/identity/{agent_identity_id}"
        ),
        get_usage_attribution=lambda: "signed-file-attribution",
    )
    monkeypatch.setattr(
        "aion.api.file_service_client.get_aion_runtime_context",
        lambda: runtime_context,
    )

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/files"
        assert request.url.params["organizationId"] == "organization-1"
        assert request.url.params["purpose"] == "MessagingMedia"
        assert request.url.params["byteSize"] == "5"
        assert request.headers["Authorization"] == "Bearer version-token"
        assert request.headers[AION_PRINCIPAL_SELECTOR_HEADER] == (
            f"aion://agent/identity/{agent_identity_id}"
        )
        assert request.headers[AION_USAGE_ATTRIBUTION_HEADER] == (
            "signed-file-attribution"
        )
        assert b"media" in request.content
        return httpx.Response(200, json={"id": "file-1"})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    )
    client = AionFileClient(
        jwt_manager=StaticTokenManager(),
        base_url="https://api.aion.test",
        http_client=http_client,
    )

    result = await client.create(
        b"media",
        organization_id="organization-1",
        purpose="MessagingMedia",
        file_name="message.bin",
    )

    assert result == {"id": "file-1"}
    await http_client.aclose()


@pytest.mark.anyio("asyncio")
async def test_replace_preserves_explicit_attribution_headers() -> None:
    """Explicit nested context should be retained on File replacement."""
    file_id = uuid4()
    expected_version_id = uuid4()
    agent_identity_id = uuid4()

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == f"/files/{file_id}"
        assert request.url.params["expectedVersionId"] == str(
            expected_version_id
        )
        assert request.url.params["expectedRevision"] == "4"
        assert request.headers[AION_PRINCIPAL_SELECTOR_HEADER] == (
            f"aion://agent/identity/{agent_identity_id}"
        )
        assert request.headers[AION_USAGE_ATTRIBUTION_HEADER] == "carrier"
        return httpx.Response(200, json={"revision": 5})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    )
    client = AionFileClient(
        jwt_manager=StaticTokenManager(),
        base_url="https://api.aion.test",
        http_client=http_client,
    )

    result = await client.replace(
        file_id,
        b"replacement",
        expected_version_id=expected_version_id,
        expected_revision=4,
        file_name="message.bin",
        principal_selector=PrincipalSelector.agent_identity(
            str(agent_identity_id)
        ),
        usage_attribution="carrier",
    )

    assert result == {"revision": 5}
    await http_client.aclose()
