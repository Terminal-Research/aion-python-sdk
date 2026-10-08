"""ADK artifacts reach storage through the shared outbound file transformer.

The converter emits an artifact loaded from ADK's artifact service with its
content inline and the authoring helper's file action on the part. The
executor's event pipeline stores it like any other outbound file, so storage
failures follow the same policy whichever framework produced the file. An
artifact the server could not store is then removed from the artifact service.
"""

from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

import pytest
from a2a.types import Artifact, Part, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.adk.authoring.constants import AION_OUTPUT_KEY
from aion.adk.authoring.invocation.event_metadata import AionOutput, ArtifactOutput
from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
from aion.core.a2a import FileActionPayload, file_artifact
from aion.core.config.models import AgentConfig
from aion.core.constants import MESSAGING_EXTENSION_URI_V1
from aion.core.runtime.context import (
    AionRuntimeContext,
    AionRuntimeContextRegistry,
    ForwardedAttribution,
)
from aion.server.agent.execution.context.providers import (
    RequestScopeRuntimeContextProvider,
)
from aion.server.agent.adapters import ExecutionConfig, StateScope
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import (
    FileRetentionDefault,
    FileUploadErrorCode,
    FileUploadManager,
)
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.genai import types

from tests.unit.support.files import (
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
    """Install the accepted callback evidence that execution always supplies."""
    payload = distribution_payload(principal())
    AionRuntimeContextRegistry.set_current_context(
        AionRuntimeContext(distribution_extension_payload=payload,
                           callback_attribution=ForwardedAttribution("signed"))
    )
    return payload


@pytest.fixture
def no_runtime_context(context_provider):
    AionRuntimeContextRegistry.set_current_context(None)


def bytes_artifact_event(
    data: bytes = b"hello bytes", *, file_action: FileActionPayload | None = None,
) -> tuple[Event, MagicMock]:
    artifact = file_artifact(data, mime_type="text/plain", name="bytes_file", file_action=file_action)
    event = Event(
        author="agent",
        content=None,
        partial=False,
        actions=EventActions(artifact_delta={"bytes_file": 0}),
        custom_metadata={
            AION_OUTPUT_KEY: AionOutput(
                artifact=ArtifactOutput(
                    artifact_id=artifact.artifact_id, artifact_name="bytes_file",
                    file_action=(file_action.to_metadata()[MESSAGING_EXTENSION_URI_V1]
                                 if file_action is not None else None),
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


def converter(service) -> ADKToA2AEventConverter:
    ctx = MagicMock()
    ctx.app_name = "app"
    ctx.user_id = "user"
    ctx.session.id = "sess"
    ctx.artifact_service = service
    return ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1", ctx=ctx)


async def store(event, backend):
    """Pass a converted event through the transformer the executor's pipeline uses."""
    return await A2AFileTransformer(FileUploadManager(backend)).transform_event(event)


class TestArtifactStorage:
    async def test_the_converter_leaves_the_content_inline(self, runtime_context):
        """Storing is the pipeline's job, so it happens once, under one policy."""
        event, service = bytes_artifact_event()

        results = await converter(service).convert(event)

        assert isinstance(results[0], TaskArtifactUpdateEvent)
        assert results[0].artifact.parts[0].raw == b"hello bytes"

    @pytest.mark.parametrize("action,expected", [
        (None, FileRetentionDefault.PROVIDER),
        (FileActionPayload(), None),
        (FileActionPayload(retention_expires_at=None), None),
        (FileActionPayload(retention_expires_at="2099-01-01T00:00:00Z"),
         datetime(2099, 1, 1, tzinfo=timezone.utc)),
    ])
    async def test_adk_file_action_reaches_storage(self, runtime_context, action, expected):
        event, service = bytes_artifact_event(file_action=action)
        backend = RecordingBackend()
        (converted,) = await converter(service).convert(event)

        stored = (await store(converted, backend)).event

        assert backend.batches[0][0].retention_expires_at == expected
        assert stored.artifact.parts[0].url
        assert (MESSAGING_EXTENSION_URI_V1 in stored.artifact.extensions) == (action is not None)

    async def test_inline_artifact_content_is_stored(self, runtime_context):
        event, service = bytes_artifact_event()
        backend = RecordingBackend()
        (converted,) = await converter(service).convert(event)

        stored = (await store(converted, backend)).event

        part = stored.artifact.parts[0]
        assert part.url and not part.raw
        assert backend.batches[0][0].filename == "bytes_file"
        assert backend.contexts[0].organization_id is None
        assert backend.contexts[0].usage_attribution == "signed"
        assert backend.contexts[0].context_id == "ctx-1"

    async def test_missing_attribution_stores_nothing(self, no_runtime_context):
        """Missing invocation scope must not turn an upload into autonomous work."""
        backend = RecordingBackend()
        event, service = bytes_artifact_event()
        (converted,) = await converter(service).convert(event)

        transform = await store(converted, backend)

        assert transform.event is None
        assert [f.error_code for f in transform.report.failures] == [
            FileUploadErrorCode.NO_ATTRIBUTION
        ]
        assert backend.batches == []


class TestUndeliveredArtifact:
    """An artifact the server could not store is gone from the artifact service.

    A later turn of the context reads artifacts back through the same service,
    and must not find the bytes of one the client never received.
    """

    ALICE = StateScope(agent_id="agent", owner_scope="alice")
    BOB = StateScope(agent_id="agent", owner_scope="bob")

    @pytest.fixture
    async def artifacts(self):
        service = A2AArtifactService()
        yield service
        await service.close()

    @staticmethod
    async def _save(service, scope: StateScope, filename: str) -> None:
        await service.save_artifact(
            app_name=scope.agent_id,
            user_id=scope.state_owner,
            session_id="ctx-1",
            filename=filename,
            artifact=types.Part(inline_data=types.Blob(data=b"bytes", mime_type="text/plain")),
        )

    @staticmethod
    async def _loads(service, scope: StateScope, filename: str) -> bool:
        part = await service.load_artifact(
            app_name=scope.agent_id, user_id=scope.state_owner, session_id="ctx-1", filename=filename
        )
        return part is not None

    def _executor(self, service) -> ADKExecutor:
        return ADKExecutor(
            agent=object(),
            config=AgentConfig(path="m:agent"),
            session_service=InMemorySessionService(),
            artifact_service=service,
        )

    def _config(self, scope: StateScope) -> ExecutionConfig:
        return ExecutionConfig(task_id="task-1", context_id="ctx-1", state_scope=scope)

    async def test_the_undelivered_artifact_is_removed_and_nothing_else(self, artifacts):
        for scope, filename in ((self.ALICE, "report.pdf"), (self.ALICE, "other"), (self.BOB, "report.pdf")):
            await self._save(artifacts, scope, filename)
        event = TaskArtifactUpdateEvent(
            task_id="task-1",
            context_id="ctx-1",
            artifact=Artifact(artifact_id="a-1", name="report.pdf", parts=[Part(raw=b"bytes")]),
        )

        await self._executor(artifacts).discard_undelivered(self._config(self.ALICE), event)

        assert not await self._loads(artifacts, self.ALICE, "report.pdf")
        assert await self._loads(artifacts, self.ALICE, "other")
        assert await self._loads(artifacts, self.BOB, "report.pdf")

    async def test_a_status_update_has_nothing_in_the_artifact_service(self, artifacts):
        """Files in a status message come from the model's reply, not from saved artifacts."""
        await self._save(artifacts, self.ALICE, "report.pdf")
        event = TaskStatusUpdateEvent(task_id="task-1", context_id="ctx-1")

        await self._executor(artifacts).discard_undelivered(self._config(self.ALICE), event)

        assert await self._loads(artifacts, self.ALICE, "report.pdf")
