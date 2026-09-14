"""The ADK artifact path stores its own content, under the same policy.

Artifacts loaded from ADK's artifact service are converted directly, without
passing through the A2A part transformer. That makes this the second direct
entry into storage and the one that would otherwise let raw bytes reach the
task record - so it shares the outbound rule: never fall back to inline
content; an artifact that could not be stored is dropped and logged.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import TaskArtifactUpdateEvent
from aion.adk.authoring.constants import AION_OUTPUT_KEY
from aion.adk.authoring.invocation.event_metadata import AionOutput, ArtifactOutput
from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
from aion.core.a2a import file_artifact
from aion.core.runtime.context import (
    AionRuntimeContext,
    AionRuntimeContextRegistry,
)
from aion.server.agent.execution.context.providers import (
    RequestScopeRuntimeContextProvider,
)
from aion.server.files.storage import (
    FileUploadErrorCode,
    FileUploadManager,
    UploadFailure,
)
from google.adk.events import Event, EventActions
from google.genai import types

from tests.server.files.test_file_storage import (
    OutcomeBackend,
    RecordingBackend,
    distribution_payload,
    principal,
)


@pytest.fixture(autouse=True)
def context_provider():
    """The provider a server installs at startup; tests need it too."""
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    yield
    AionRuntimeContextRegistry.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


@pytest.fixture
def runtime_context(context_provider):
    """Install a runtime context carrying a usable distribution."""
    payload = distribution_payload(principal())
    AionRuntimeContextRegistry.set_current_context(
        AionRuntimeContext(distribution_extension_payload=payload)
    )
    return payload


@pytest.fixture
def no_runtime_context(context_provider):
    AionRuntimeContextRegistry.set_current_context(None)


def bytes_artifact_event(data: bytes = b"hello bytes") -> tuple[Event, MagicMock]:
    artifact = file_artifact(data, mime_type="text/plain", name="bytes_file")
    event = Event(
        author="agent",
        content=None,
        partial=False,
        actions=EventActions(artifact_delta={"bytes_file": 0}),
        custom_metadata={
            AION_OUTPUT_KEY: AionOutput(
                artifact=ArtifactOutput(
                    artifact_id=artifact.artifact_id, artifact_name="bytes_file"
                )
            ).model_dump(exclude_none=True)
        },
    )
    service = MagicMock()
    service.save_artifact = AsyncMock(return_value=0)
    service.load_artifact = AsyncMock(
        return_value=types.Part(
            inline_data=types.Blob(data=data, mime_type="text/plain")
        )
    )
    return event, service


def converter(service, backend=None) -> ADKToA2AEventConverter:
    ctx = MagicMock()
    ctx.app_name = "app"
    ctx.user_id = "user"
    ctx.session.id = "sess"
    ctx.artifact_service = service
    return ADKToA2AEventConverter(
        task_id="task-1",
        context_id="ctx-1",
        ctx=ctx,
        file_uploader=(
            FileUploadManager(backend) if backend is not None else None
        ),
    )


class TestArtifactStorage:
    async def test_inline_artifact_content_is_stored(self, runtime_context):
        event, service = bytes_artifact_event()
        backend = RecordingBackend()

        results = await converter(service, backend).convert(event)

        assert isinstance(results[0], TaskArtifactUpdateEvent)
        part = results[0].artifact.parts[0]
        assert part.url and not part.raw
        assert backend.contexts[0].organization_id == "org-1"
        assert backend.contexts[0].context_id == "ctx-1"

    async def test_failed_storage_drops_the_artifact(self, runtime_context, caplog):
        """Not inline bytes - that is exactly what the guard exists to prevent."""
        backend = OutcomeBackend(
            [UploadFailure(FileUploadErrorCode.STORAGE_REJECTED)]
        )
        event, service = bytes_artifact_event()

        with caplog.at_level("WARNING"):
            results = await converter(service, backend).convert(event)

        assert results == []
        assert "STORAGE_REJECTED" in caplog.text

    async def test_missing_distribution_drops_the_artifact(self, no_runtime_context):
        """No distribution means no owning organization, so nothing can be stored."""
        backend = RecordingBackend()
        event, service = bytes_artifact_event()

        results = await converter(service, backend).convert(event)

        assert results == []
        assert backend.batches == []

    async def test_without_an_uploader_content_passes_through(self, runtime_context):
        """Passthrough stays a supported mode: no backend, no conversion."""
        event, service = bytes_artifact_event()

        results = await converter(service, backend=None).convert(event)

        assert results[0].artifact.parts[0].raw == b"hello bytes"
