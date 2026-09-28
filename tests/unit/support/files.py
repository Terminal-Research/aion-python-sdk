"""Builders and stub backends for the file storage tests.

Used by the storage contract tests, the Aion backend tests and the framework
event converters, which all need the same organization, principal and
distribution payload, and the same recording stubs.
"""

from __future__ import annotations

import asyncio

from aion.core.a2a.extensions import (
    Behavior,
    Distribution,
    DistributionExtensionV1,
    Environment,
    PrincipalIdentity,
)
from aion.server.files.storage import (
    FileUpload,
    StubFileStorageBackend,
    UploadContext,
)

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
