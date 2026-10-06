from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json

from aion.core.runtime.context import AionRuntimeContext, ForwardedAttribution
from uuid import uuid4

import httpx
import pytest

from aion.api.control_plane import (
    AION_PRINCIPAL_SELECTOR_HEADER,
    PrincipalSelector,
)
from aion.api.exceptions import AionFileStorageError
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
                await client.create_agent_artifact(
                    b"media",
                    organization_id="organization-1",
                    file_name="message.bin",
                    **association,
                )

    assert isinstance(error.value, AionError)
    assert isinstance(error.value, ValueError)


@pytest.mark.anyio("asyncio")
async def test_create_forwards_runtime_attribution_without_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested File create keeps Version credentials and the opaque carrier."""
    agent_identity_id = uuid4()
    runtime_context = AionRuntimeContext(
        callback_attribution=ForwardedAttribution("signed-file-attribution")
    )
    monkeypatch.setattr(
        "aion.api.callback_attribution.get_aion_runtime_context",
        lambda: runtime_context,
    )

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/files/agent-artifacts"
        assert request.url.params["organizationId"] == "organization-1"
        assert "purpose" not in request.url.params
        assert request.url.params["byteSize"] == "5"
        assert request.headers["Authorization"] == "Bearer version-token"
        assert AION_PRINCIPAL_SELECTOR_HEADER not in request.headers
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

    result = await client.create_agent_artifact(
        b"media",
        organization_id="organization-1",
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
        assert AION_PRINCIPAL_SELECTOR_HEADER not in request.headers
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
        usage_attribution="carrier",
    )

    assert result == {"revision": 5}
    await http_client.aclose()


@pytest.mark.anyio("asyncio")
async def test_rejected_upload_is_both_an_sdk_and_an_httpx_error():
    """A rejection must stay catchable both ways.

    Callers already classify Files failures by ``response.status_code`` through
    ``httpx.HTTPStatusError``; SDK-wide handlers catch ``AionError``. The
    wrapper is both, so neither has to change.
    """
    async def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "invalid file"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(rejected)
    ) as http_client:
        async with AionFileClient(
            jwt_manager=StaticTokenManager(),
            base_url="https://api.aion.test",
            http_client=http_client,
        ) as client:
            with pytest.raises(AionFileStorageError) as error:
                await client.create_agent_artifact(
                    b"media",
                    organization_id="organization-1",
                    file_name="message.bin",
                )

    assert isinstance(error.value, AionError)
    assert isinstance(error.value, httpx.HTTPStatusError)
    assert error.value.response.status_code == 422
    assert error.value.request is not None


@pytest.mark.anyio("asyncio")
async def test_rejection_names_what_the_api_said():
    """The status alone does not say why; the body and request id do."""
    async def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="AgentIdentity lacks\n  file.create " + "x" * 400,
            headers={"x-request-id": "req-42"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(rejected)
    ) as http_client:
        async with AionFileClient(
            jwt_manager=StaticTokenManager(),
            base_url="https://api.aion.test",
            http_client=http_client,
        ) as client:
            with pytest.raises(AionFileStorageError) as error:
                await client.create_agent_artifact(
                    b"media",
                    organization_id="organization-1",
                    file_name="message.bin",
                )

    message = str(error.value)
    assert "\n" not in message
    assert message.startswith("Client error '403 Forbidden'")
    assert "AgentIdentity lacks file.create" in message
    assert "(request id req-42)" in message
    assert error.value.request_id == "req-42"
    assert error.value.detail.startswith("AgentIdentity lacks file.create")
    assert len(error.value.detail) == 300
    assert "version-token" not in message


@pytest.mark.anyio("asyncio")
async def test_rejection_without_body_or_request_id_keeps_the_status_line():
    async def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(rejected)
    ) as http_client:
        async with AionFileClient(
            jwt_manager=StaticTokenManager(),
            base_url="https://api.aion.test",
            http_client=http_client,
        ) as client:
            with pytest.raises(AionFileStorageError) as error:
                await client.create_agent_artifact(
                    b"media",
                    organization_id="organization-1",
                    file_name="message.bin",
                )

    assert error.value.detail is None
    assert error.value.request_id is None
    assert str(error.value).startswith("Client error '403 Forbidden' for url ")
    assert error.value.request.url.params["byteSize"] == "5"


def test_content_url_names_the_exact_version():
    client = AionFileClient(
        jwt_manager=StaticTokenManager(), base_url="https://api.aion.test/"
    )
    assert client.content_url("file-1", "version-2") == (
        "https://api.aion.test/files/file-1/versions/version-2/content"
    )


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("ttl,seconds", [(None, None), (1, "60"), (60, "3600"), (1440, "86400")])
async def test_grants_convert_minutes_once_and_preserve_exact_ids(ttl, seconds):
    async def handle(request):
        assert request.url.path == "/files/f-1/versions/v-2/grants"
        assert request.url.params.get("ttlSeconds") == seconds
        assert request.headers["Authorization"] == "Bearer version-token"
        assert request.headers[AION_USAGE_ATTRIBUTION_HEADER] == "carrier"
        return httpx.Response(200, json={
            "id": "f-1", "versionId": "v-2", "url": "https://api.aion.test/granted",
            "accessExpiresAt": "2026-10-06T01:00:00Z", "retentionExpiresAt": None,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with AionFileClient(jwt_manager=StaticTokenManager(),
                                  base_url="https://api.aion.test", http_client=http) as files:
            result = await files.create_read_grant(
                "f-1", "v-2", ttl_minutes=ttl, usage_attribution="carrier",
            )
    assert result["versionId"] == "v-2"
    assert result["retentionExpiresAt"] is None
    assert result["accessExpiresAt"] == "2026-10-06T01:00:00Z"


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("ttl", [0, -1, 1441, 1.5, True, "60"])
async def test_invalid_grant_lifetimes_never_reach_http(ttl):
    async def unexpected(request):
        pytest.fail("Invalid grant lifetime reached HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        async with AionFileClient(jwt_manager=StaticTokenManager(), http_client=http) as files:
            with pytest.raises(AionFileValidationError):
                await files.create_read_grant("f", "v", ttl_minutes=ttl)


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("deadline", [
    None, datetime(2100, 1, 1, tzinfo=timezone(timedelta(hours=2))),
])
async def test_explicit_retention_preserves_absolute_value_and_null(deadline):
    bodies = []

    async def handle(request):
        assert request.method == "PUT"
        assert request.url.path == "/files/f/retention"
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"metadata": bodies[-1]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with AionFileClient(jwt_manager=StaticTokenManager(), http_client=http) as files:
            for _ in range(2):
                await files.renew_retention("f", retention_expires_at=deadline)
            with pytest.raises(TypeError):
                await files.renew_retention("f")
    expected = None if deadline is None else "2099-12-31T22:00:00Z"
    assert bodies == [{"retentionExpiresAt": expected}] * 2


@pytest.mark.anyio("asyncio")
async def test_naive_retention_deadline_never_reaches_http():
    async def unexpected(request):
        pytest.fail("Naive date reached HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        async with AionFileClient(jwt_manager=StaticTokenManager(), http_client=http) as files:
            with pytest.raises(AionFileValidationError):
                await files.renew_retention("f", retention_expires_at=datetime(2030, 1, 1))


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("image_type", ["avatar", "background"])
async def test_profile_routes_carry_targets_without_policy_overrides(image_type):
    async def handle(request):
        assert request.url.path == f"/files/profile-images/{image_type}"
        assert request.url.params["associationKind"] == "Identity"
        assert request.url.params["associationId"] == "service-1"
        assert "purpose" not in request.url.params
        assert "retentionExpiresAt" not in request.url.params
        return httpx.Response(200, json={"id": "image-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with AionFileClient(jwt_manager=StaticTokenManager(), http_client=http) as files:
            await files.create_profile_image(
                b"image", image_type=image_type, organization_id="org",
                file_name="image.png", media_type="image/png",
                association_kind="Identity", association_id="service-1",
            )


@pytest.mark.anyio("asyncio")
async def test_file_metadata_preserves_daemon_configuration_error():
    from aion.core.exceptions import AionDaemonIdentityRequired

    async def handle(request):
        assert request.method == "GET"
        return httpx.Response(409, json={
            "error": {"code": "daemon_identity_required", "resourceType": "Deployment"}
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with AionFileClient(jwt_manager=StaticTokenManager(), http_client=http) as files:
            with pytest.raises(AionDaemonIdentityRequired):
                await files.get_metadata("f")


@pytest.mark.anyio("asyncio")
async def test_artifact_deadline_is_explicit_and_metadata_does_not_renew_it():
    seen = []
    deadline = datetime(2100, 1, 1, tzinfo=timezone.utc)

    async def handle(request):
        seen.append(request)
        if request.method == "POST":
            assert request.url.params["retentionExpiresAt"] == "2100-01-01T00:00:00Z"
            assert "purpose" not in request.url.params
            assert "storageCategory" not in request.url.params
        return httpx.Response(200, json={"id": "f"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with AionFileClient(jwt_manager=StaticTokenManager(), http_client=http) as files:
            await files.create_agent_artifact(
                b"x", organization_id="org", file_name="x.txt",
                retention_expires_at=deadline,
            )
            await files.get_metadata("f")
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/files/agent-artifacts"), ("GET", "/files/f"),
    ]
