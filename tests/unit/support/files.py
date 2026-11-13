"""Builders and stub backends for the file storage tests.

Used by the storage contract tests, the Aion backend tests and the framework
event converters, which all need the same organization, principal and
distribution payload, and the same recording stubs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from aion.core.a2a.extensions import (
    Behavior,
    Distribution,
    DistributionExtensionV1,
    Environment,
    PrincipalIdentity,
)
from aion.core.runtime.context import AionRuntimeContext, AionRuntimeContextRegistry, ForwardedAttribution
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import (
    FileUpload,
    FileUploadErrorCode,
    FileUploadManager,
    StubFileStorageBackend,
    UploadContext,
    UploadFailure,
    UploadReceipt,
)
from aion.server.files.turn import TurnFiles, turn_files

ORG = "org-1"


def principal(
    organization_id: str = ORG, identity_id: str = "pid-1"
) -> PrincipalIdentity:
    return PrincipalIdentity(
        kind="principal",
        id=identity_id,
        identity_network="Aion",
        identity_kind="Principal",
        organization_id=organization_id,
    )


def distribution_payload(
    *identities,
    daemon_identity_id: str | None = None,
) -> DistributionExtensionV1:
    return DistributionExtensionV1(
        distribution=Distribution(
            id="dist-1",
            endpoint_type="A2A",
            url="https://example.invalid/a2a",
            identities=list(identities),
        ),
        behavior=Behavior(id="b-1", behavior_key="main", version_id="v-1"),
        environment=Environment(
            id="env-1",
            name="prod",
            project_id="proj-1",
            deployment_id="dep-1",
            configuration_variables={},
            daemon_agent_identity_id=daemon_identity_id,
        ),
    )


def upload_context(**overrides) -> UploadContext:
    return UploadContext(organization_id=ORG, **overrides)


class RecordingBackend(StubFileStorageBackend):
    """Stub that records every batch it was handed."""

    def __init__(self):
        self.batches: list[list[FileUpload]] = []
        self.contexts: list[UploadContext] = []

    async def store_many(self, uploads, *, context):
        self.batches.append(list(uploads))
        self.contexts.append(context)
        return await super().store_many(uploads, context=context)


class OutcomeBackend(StubFileStorageBackend):
    """Stub returning a scripted outcome per position, with a delay each."""

    def __init__(self, outcomes, delay: float = 0.0):
        self._outcomes = outcomes
        self._delay = delay
        self.concurrent_peak = 0
        self._in_flight = 0

    async def store_many(self, uploads, *, context):
        async def one(index):
            self._in_flight += 1
            self.concurrent_peak = max(self.concurrent_peak, self._in_flight)
            try:
                if self._delay:
                    await asyncio.sleep(self._delay)
                return self._outcomes[index]
            finally:
                self._in_flight -= 1

        return list(await asyncio.gather(*(one(i) for i in range(len(uploads)))))


class StoringTransformer(A2AFileTransformer):
    """The real file transformer, storing against a fixed organization.

    Its callers leave the upload context to the transformer, which projects it
    from the request; a unit test has no request, so the context is given.
    """

    def __init__(self, backend=None) -> None:
        super().__init__(upload_manager=FileUploadManager(backend or StubFileStorageBackend()))

    async def transform(self, event, **kwargs):
        return await super().transform(event, upload_context=upload_context(), **kwargs)


class NumberingBackend(StubFileStorageBackend):
    """Stub that stores each file as the next number - file ``file-<n>``, version ``ver-<n>`` - or refuses them all.

    ``stored`` is every file it stored, in order; ``delay`` holds each batch
    back, so that two asks can overlap.
    """

    def __init__(self, *, refuse: bool = False, delay: float = 0.0) -> None:
        self.refuse = refuse
        self.delay = delay
        self.stored: list[FileUpload] = []

    async def store_many(self, uploads, *, context):
        if self.delay:
            await asyncio.sleep(self.delay)
        outcomes = []
        for upload in uploads:
            if self.refuse:
                outcomes.append(UploadFailure(FileUploadErrorCode.STORAGE_REJECTED))
                continue
            self.stored.append(upload)
            number = len(self.stored)
            outcomes.append(
                UploadReceipt(
                    uri=f"https://files.test/{number}?grant=g",
                    file_id=f"file-{number}",
                    version_id=f"ver-{number}",
                    access_expires_at="2099-01-01T00:00:00Z",
                )
            )
        return outcomes


@contextmanager
def storing_turn(backend: StubFileStorageBackend, *, task_id: str = "task-1") -> Iterator[TurnFiles]:
    """A turn whose files ``backend`` stores, with the callback attribution an execution always has."""
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    AionRuntimeContextRegistry.set_current_context(
        AionRuntimeContext(
            distribution_extension_payload=distribution_payload(principal()),
            callback_attribution=ForwardedAttribution("signed"),
        )
    )
    files = TurnFiles(
        A2AFileTransformer(FileUploadManager(backend)), context_id="context-1", task_id=task_id
    )
    try:
        with turn_files(files):
            yield files
    finally:
        AionRuntimeContextRegistry.set_current_context(None)
        AionRuntimeContextRegistry.clear_provider()
