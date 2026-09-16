"""The Aion Files API backend: request shape, retry, and failure classification.

Every test speaks to a mocked transport - nothing here reaches the platform.
"""

from __future__ import annotations

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

from tests.support.files import ORG, upload_context


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
            uri="https://files.aion.test/file-1",
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

    async def test_request_acts_as_the_projected_principal(self):
        seen: list[httpx.Request] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return accepted()

        context = upload_context(
            principal_selector=PrincipalSelector.agent_identity("daemon-7"),
            usage_attribution="opaque-carrier",
        )
        await backend(handle).store(upload(), context=context)

        headers = seen[0].headers
        assert headers["Aion-Principal-Selector"] == "aion://agent/identity/daemon-7"
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
    async def test_refused_credentials_are_reported_once(self, caplog):
        """A broken secret fails every upload the same way; say it once."""
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        b = backend(handle)
        with caplog.at_level("DEBUG"):
            first = await b.store(upload(), context=upload_context())
            second = await b.store(upload(), context=upload_context())

        assert first.error_code is second.error_code is FileUploadErrorCode.STORAGE_UNAUTHORIZED
        assert first.retryable is False and first.client_fault is False
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1

    async def test_diagnostic_is_rearmed_by_a_success(self, caplog):
        answers = iter([httpx.Response(403), accepted(), httpx.Response(403)])

        async def handle(request: httpx.Request) -> httpx.Response:
            return next(answers)

        b = backend(handle)
        with caplog.at_level("ERROR"):
            for _ in range(3):
                await b.store(upload(), context=upload_context())

        assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 2

    async def test_no_token_means_no_request(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            pytest.fail("must not reach the API without a token")

        outcome = await backend(handle, token=None).store(upload(), context=upload_context())
        assert outcome.error_code is FileUploadErrorCode.STORAGE_UNAUTHORIZED


class TestResponseContract:
    async def test_response_without_a_url_is_a_failure(self, caplog):
        """A stored file nobody can reach is worse than none: say so, loudly."""
        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "file-1", "secretUrl": "https://x/?sig=s3cr3t"})

        with caplog.at_level("ERROR"):
            outcome = await backend(handle).store(upload(), context=upload_context())

        assert isinstance(outcome, UploadFailure)
        assert outcome.retryable is False
        assert "secretUrl" in caplog.text
        assert "s3cr3t" not in caplog.text


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
