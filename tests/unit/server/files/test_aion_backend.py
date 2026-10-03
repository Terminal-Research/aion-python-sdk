"""The Aion Files API backend: request shape, retry, and failure classification.

Every test speaks to a mocked transport - nothing here reaches the platform.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from aion.api import AionFileClient, PrincipalSelector
from aion.server.files.storage import (
    FileUpload,
    FileUploadErrorCode,
    FileUploadManager,
    UploadFailure,
    UploadReceipt,
)
from aion.server.files.storage.backends.aion import (
    AION_FILE_PURPOSE,
    AionFileStorageBackend,
)

from tests.unit.support.files import ORG, upload_context


class StaticTokenManager:
    def __init__(self, token: str | None = "token") -> None:
        self._token = token

    async def get_token(self) -> str | None:
        return self._token


def accepted(file_id: str = "file-1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": file_id,
            "url": f"https://files.aion.test/{file_id}",
            "versionId": "v-1",
            "revision": 1,
        },
    )


def backend(handler, *, token="token", attempts=3) -> AionFileStorageBackend:
    client = AionFileClient(
        jwt_manager=StaticTokenManager(token),
        base_url="https://api.aion.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return AionFileStorageBackend(client, max_attempts=attempts, backoff_seconds=0)


def upload(name="report.pdf", data=b"%PDF") -> FileUpload:
    return FileUpload(data=data, media_type="application/pdf", filename=name)


class TestRequestShape:
    async def test_stored_file_becomes_a_receipt(self):
        seen: list[httpx.Request] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return accepted()

        outcome = await backend(handle).store(upload(), context=upload_context())

        assert outcome == UploadReceipt(
            uri="https://api.aion.test/files/file-1/versions/v-1/content",
            file_id="file-1",
            version_id="v-1",
            revision=1,
        )
        request = seen[0]
        assert request.method == "POST" and request.url.path == "/files"
        assert request.url.params["organizationId"] == ORG
        assert request.url.params["purpose"] == AION_FILE_PURPOSE
        assert request.url.params["byteSize"] == "4"
        assert request.url.params["operationId"]
        assert request.headers["Authorization"] == "Bearer token"

    async def test_request_forwards_carrier_without_claiming_a_principal(self):
        seen: list[httpx.Request] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return accepted()

        context = upload_context(
            usage_attribution="opaque-carrier",
        )
        await backend(handle).store(upload(), context=context)

        headers = seen[0].headers
        assert "Aion-Principal-Selector" not in headers
        assert headers["Aion-Usage-Attribution"] == "opaque-carrier"

    async def test_file_name_is_sanitized_before_it_leaves(self):
        seen: list[bytes] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return accepted()

        await backend(handle).store(
            upload(name='../../x y;"z".pdf'), context=upload_context()
        )
        assert b'filename="x-y-z.pdf"' in seen[0]
        assert b"../" not in seen[0]

    async def test_batch_outcomes_keep_their_positions(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            if b"second" in request.content:
                return httpx.Response(422, json={"message": "no"})
            return accepted()

        outcomes = await backend(handle).store_many(
            [upload(data=b"first"), upload(data=b"second"), upload(data=b"third")],
            context=upload_context(),
        )
        assert isinstance(outcomes[0], UploadReceipt)
        assert isinstance(outcomes[1], UploadFailure)
        assert isinstance(outcomes[2], UploadReceipt)


class TestRetry:
    async def test_operation_id_is_stable_across_attempts(self):
        """A retry after a lost answer must not create a second File."""
        ids: list[str] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            ids.append(request.url.params["operationId"])
            return httpx.Response(503) if len(ids) < 3 else accepted()

        outcome = await backend(handle).store(upload(), context=upload_context())

        assert isinstance(outcome, UploadReceipt)
        assert len(set(ids)) == 1 and len(ids) == 3

    async def test_unavailable_after_the_last_attempt(self):
        calls = 0

        async def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503)

        outcome = await backend(handle, attempts=2).store(upload(), context=upload_context())

        assert outcome.error_code is FileUploadErrorCode.STORAGE_UNAVAILABLE
        assert outcome.retryable is True
        assert calls == 2

    async def test_transport_errors_are_retried(self):
        calls = 0

        async def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("refused", request=request)
            return accepted()

        outcome = await backend(handle).store(upload(), context=upload_context())
        assert isinstance(outcome, UploadReceipt)
        assert calls == 2

    async def test_a_rejection_is_not_retried(self):
        calls = 0

        async def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(422, json={"message": "bad purpose"})

        outcome = await backend(handle).store(upload(), context=upload_context())

        assert outcome.error_code is FileUploadErrorCode.STORAGE_REJECTED
        assert outcome.retryable is False
        assert outcome.client_fault is True
        assert calls == 1


class TestCredentials:
    async def test_refused_credentials_name_themselves(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        outcome = await backend(handle).store(upload(), context=upload_context())

        assert outcome.error_code is FileUploadErrorCode.STORAGE_UNAUTHORIZED
        assert outcome.retryable is False and outcome.client_fault is False
        assert "refuses the agent's credentials (HTTP 401)" in str(outcome.cause)

    async def test_the_backend_logs_nothing_itself(self, caplog):
        """Whoever drops the file writes its one line; a second would repeat it."""
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        with caplog.at_level("INFO", logger="aion.server.files.storage.backends.aion"):
            await backend(handle).store(upload(), context=upload_context())

        assert not [r for r in caplog.records if r.name.endswith("backends.aion")]

    async def test_no_token_means_no_request(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            pytest.fail("must not reach the API without a token")

        outcome = await backend(handle, token=None).store(upload(), context=upload_context())
        assert outcome.error_code is FileUploadErrorCode.STORAGE_UNAUTHORIZED


class TestPermissions:
    """A 403 is a principal the API will not let write here, not a bad secret."""

    async def test_a_refused_principal_is_named_with_its_organization(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden", headers={"x-request-id": "req-7"})

        context = upload_context(usage_attribution="opaque")
        outcome = await backend(handle).store(upload(), context=context)

        assert outcome.error_code is FileUploadErrorCode.STORAGE_FORBIDDEN
        assert outcome.retryable is False and outcome.client_fault is False
        reason = str(outcome.cause)
        assert "resolved identity" in reason and ORG in reason
        assert "HTTP 403: Forbidden (request id req-7)" in reason
        assert "credentials" not in reason and "Daemon Identity" not in reason
        assert isinstance(outcome.cause.__cause__, httpx.HTTPStatusError)

    async def test_permission_error_does_not_guess_the_resolved_principal(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        context = upload_context(usage_attribution="opaque")
        outcome = await backend(handle).store(upload(), context=context)

        assert "resolved identity" in str(outcome.cause)
        assert "no Daemon Identity" not in str(outcome.cause)


class TestResponseContract:
    async def test_response_without_a_url_is_a_failure(self, caplog):
        """A stored file nobody can reach is worse than none: say so, loudly."""
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "file-1", "secretUrl": "https://x/?sig=s3cr3t"})

        with caplog.at_level("WARNING"):
            outcome = await backend(handle).store(upload(), context=upload_context())

        assert isinstance(outcome, UploadFailure)
        assert outcome.retryable is False
        assert "secretUrl" in caplog.text
        assert "s3cr3t" not in caplog.text


class TestStoredAddress:
    """The part points at the stored version, not at a link that expires."""

    async def test_a_granted_url_in_the_response_is_not_persisted(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"id": "f-1", "versionId": "v-9", "url": "https://x/f-1?grant=g"},
            )

        outcome = await backend(handle).store(upload(), context=upload_context())

        assert outcome.uri == "https://api.aion.test/files/f-1/versions/v-9/content"

    async def test_without_a_version_the_response_url_is_used(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "f-1", "url": "https://files.aion.test/f-1"})

        outcome = await backend(handle).store(upload(), context=upload_context())

        assert outcome.uri == "https://files.aion.test/f-1"
        assert outcome.version_id is None


class TestLifecycle:
    async def test_from_settings_refuses_to_start_without_credentials(self, monkeypatch):
        from aion.core.settings import api_settings
        from aion.server.settings import app_settings

        monkeypatch.setattr(app_settings, "file_storage_backend", "aion")
        monkeypatch.setattr(api_settings, "client_id", None)
        monkeypatch.setattr(api_settings, "client_secret", "s")

        with pytest.raises(ValueError, match="AION_CLIENT_ID"):
            FileUploadManager.from_settings()

    async def test_from_settings_builds_the_backend_with_credentials(self, monkeypatch):
        from aion.core.settings import api_settings
        from aion.server.settings import app_settings

        monkeypatch.setattr(app_settings, "file_storage_backend", "aion")
        monkeypatch.setattr(api_settings, "client_id", "id")
        monkeypatch.setattr(api_settings, "client_secret", "s")

        manager = FileUploadManager.from_settings()
        assert isinstance(manager._backend, AionFileStorageBackend)
        await manager.aclose()

    async def test_aclose_closes_the_client(self):
        closed = []

        class Client(AionFileClient):
            async def aclose(self) -> None:
                closed.append(True)

        client = Client(jwt_manager=StaticTokenManager(), base_url="https://api.aion.test")
        await AionFileStorageBackend(client).aclose()
        assert closed == [True]
