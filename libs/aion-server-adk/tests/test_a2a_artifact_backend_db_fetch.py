"""Tests for how A2AArtifactService asks the repository for artifact versions.

``_fetch_db_artifacts`` serves callers with two different needs from the same
DB round trip: some (``list_versions`` and friends) want a named artifact's
full version history, others (``_build_artifact_version_from_db``,
``_load_from_db``) only ever read the first entry back out. ``latest_only``
is what tells the repository which one it is, so it can stop at the first
matching task row instead of scanning every version - these tests pin the
wiring that decides which mode each caller gets.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService


def make_service() -> A2AArtifactService:
    db_manager = MagicMock()
    db_manager.is_initialized = True
    return A2AArtifactService(db_manager=db_manager)


def patched_repository():
    """Patch the module's TasksRepository constructor to return a stub repo."""
    repo = MagicMock()
    repo.find_artifacts = AsyncMock(return_value=[])
    return patch(
        "aion.adk.server.artifacts.backends.a2a.TasksRepository",
        return_value=repo,
    ), repo


async def test_latest_only_asks_the_repository_for_the_sentinel_version():
    service = make_service()
    patcher, repo = patched_repository()
    with patcher:
        await service._fetch_db_artifacts(
            session_id="ctx-1", filename="report", version=None, latest_only=True
        )

    assert repo.find_artifacts.await_args.kwargs["artifact_version"] == "-1"


async def test_default_call_still_asks_for_the_full_history():
    """The callers that enumerate versions (list_versions and friends) never
    set latest_only - they must keep getting every version back, not just
    the newest."""
    service = make_service()
    patcher, repo = patched_repository()
    with patcher:
        await service._fetch_db_artifacts(session_id="ctx-1", filename="report")

    assert repo.find_artifacts.await_args.kwargs["artifact_version"] is None


async def test_a_specific_version_wins_over_latest_only():
    """version=3 means "give me version 3", regardless of latest_only - the
    two must never be requested at once."""
    service = make_service()
    patcher, repo = patched_repository()
    with patcher:
        await service._fetch_db_artifacts(
            session_id="ctx-1", filename="report", version=3, latest_only=True
        )

    assert repo.find_artifacts.await_args.kwargs["artifact_version"] == "3"


async def test_build_artifact_version_from_db_requests_latest_only():
    service = make_service()
    patcher, repo = patched_repository()
    with patcher:
        await service._build_artifact_version_from_db(
            app_name="app", user_id="user", session_id="ctx-1",
            filename="report", version=None,
        )

    assert repo.find_artifacts.await_args.kwargs["artifact_version"] == "-1"


async def test_load_from_db_requests_latest_only():
    service = make_service()
    patcher, repo = patched_repository()
    with patcher:
        await service._load_from_db(session_id="ctx-1", filename="report", version=None)

    assert repo.find_artifacts.await_args.kwargs["artifact_version"] == "-1"
