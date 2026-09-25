"""``A2AArtifactService`` as an ADK agent uses it: save, load, list, versions.

Without a database the service is its in-memory half; the database half is
reached only when memory has nothing, and the offset it contributes is the
one place the two meet - tested here through the method that asks for it.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from google.adk.errors.input_validation_error import InputValidationError
from google.genai import types

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService

APP, USER, SESSION = "app", "user", "ctx-1"


@pytest.fixture
async def service() -> AsyncIterator[A2AArtifactService]:
    artifact_service = A2AArtifactService()
    try:
        yield artifact_service
    finally:
        await artifact_service.close()


async def _save(service: A2AArtifactService, filename: str, text: str, session_id=SESSION) -> int:
    return await service.save_artifact(
        app_name=APP, user_id=USER, filename=filename, artifact=types.Part(text=text), session_id=session_id
    )


async def test_versions_count_up_from_zero(service: A2AArtifactService) -> None:
    assert [await _save(service, "report.txt", text) for text in ("one", "two", "three")] == [0, 1, 2]


async def test_load_returns_the_latest_or_the_version_asked_for(service: A2AArtifactService) -> None:
    await _save(service, "report.txt", "one")
    await _save(service, "report.txt", "two")

    latest = await service.load_artifact(app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION)
    first = await service.load_artifact(
        app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION, version=0
    )

    assert latest.text == "two"
    assert first.text == "one"


async def test_versions_and_their_metadata_are_listed(service: A2AArtifactService) -> None:
    await _save(service, "report.txt", "one")
    await service.save_artifact(
        app_name=APP,
        user_id=USER,
        filename="report.txt",
        artifact=types.Part(inline_data=types.Blob(mime_type="image/png", data=b"png")),
        session_id=SESSION,
        custom_metadata={"source": "tool"},
    )

    versions = await service.list_artifact_versions(
        app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION
    )

    assert await service.list_versions(
        app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION
    ) == [0, 1]
    assert [version.mime_type for version in versions] == ["text/plain", "image/png"]
    assert versions[1].custom_metadata == {"source": "tool"}
    assert versions[1].canonical_uri.endswith(f"/sessions/{SESSION}/artifacts/report.txt/versions/1")


async def test_keys_are_the_sessions_own_plus_the_users(service: A2AArtifactService) -> None:
    await _save(service, "b.txt", "x")
    await _save(service, "a.txt", "x")
    await _save(service, "user:profile.txt", "x", session_id=None)
    await _save(service, "other.txt", "x", session_id="ctx-2")

    keys = await service.list_artifact_keys(app_name=APP, user_id=USER, session_id=SESSION)

    assert keys == ["a.txt", "b.txt", "user:profile.txt"]


async def test_a_session_artifact_needs_a_session(service: A2AArtifactService) -> None:
    with pytest.raises(InputValidationError):
        await _save(service, "report.txt", "x", session_id=None)


async def test_an_empty_artifact_loads_as_none(service: A2AArtifactService) -> None:
    await _save(service, "report.txt", "")

    assert await service.load_artifact(
        app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION
    ) is None


async def test_delete_forgets_every_version(service: A2AArtifactService) -> None:
    await _save(service, "report.txt", "one")

    await service.delete_artifact(app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION)

    assert await service.list_versions(
        app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION
    ) == []


async def test_numbering_continues_after_what_the_database_already_holds(
    service: A2AArtifactService,
) -> None:
    """A new process saves version 2, not 0, when the database holds 0 and 1."""
    service._resolve_db_version_offset = AsyncMock(return_value=2)

    version = await _save(service, "report.txt", "three")

    assert version == 2
    assert await service.list_versions(
        app_name=APP, user_id=USER, filename="report.txt", session_id=SESSION
    ) == [0, 1, 2]
