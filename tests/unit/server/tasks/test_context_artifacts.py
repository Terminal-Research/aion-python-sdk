"""The versions of an artifact among the tasks of a context, read from the in-memory store.

The PostgreSQL reader is held to the same contract in
``tests/integration/server/tasks/test_context_artifacts_parity.py``.
"""

from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.types import Artifact, Part, Task, TaskState, TaskStatus

from aion.server.tasks.admission import DELETING, ContextHolder, InMemoryContextAdmission
from aion.server.tasks.context_artifacts import InMemoryContextArtifacts, artifact_version
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

GATEWAY = ContextHolder.shared("8e7d6c5b-4a39-4281-b7c6-d5e4f3a2b1c0", "2b4c6d8e-0f1a-4b3c-8d5e-7f9a1b2c3d4e")
ALICE = ContextHolder.private("alice")


def versioned(artifact_id: str, version: object) -> Artifact:
    artifact = Artifact(artifact_id=artifact_id, parts=[Part(text=f"v{version}")])
    artifact.metadata["version"] = version
    return artifact


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


class Setup:
    def __init__(self) -> None:
        self.store = InMemoryTaskStore(resolve_user_scope)
        self.admission = InMemoryContextAdmission()
        self.ledger = InMemoryContextArtifacts(self.store, self.admission)

    async def write(self, task_id: str, *artifacts: Artifact, holder: ContextHolder = ALICE, context_id: str = "ctx"):
        assert await self.admission.admit(context_id, holder, None)
        task = Task(
            id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=list(artifacts),
        )
        await self.store.save(task, ServerCallContext())


async def test_the_versions_come_newest_task_first() -> None:
    setup = Setup()
    await setup.write("t1", versioned("spec", "0"))
    await setup.write("t2", versioned("spec", "1"), versioned("other", "0"))
    await setup.write("t3", versioned("spec", "2"))

    versions = await setup.ledger.versions("ctx", "spec", ALICE)

    assert [(item.task_id, item.version) for item in versions] == [("t3", 2), ("t2", 1), ("t1", 0)]
    assert [item.artifact.artifact_id for item in versions] == ["spec"] * 3
    assert all(item.updated_at is not None for item in versions)


async def test_only_the_holder_of_the_context_sees_its_versions() -> None:
    setup = Setup()
    await setup.write("t1", versioned("spec", "0"))

    assert await setup.ledger.versions("ctx", "spec", ContextHolder.private("bob")) == []
    assert await setup.ledger.versions("ctx", "spec", GATEWAY) == []
    assert await setup.ledger.versions("unreserved", "spec", ALICE) == []


async def test_a_shared_context_shares_the_tasks_of_all_its_participants() -> None:
    setup = Setup()
    await setup.admission.admit("ctx", GATEWAY, "alice")
    await setup.admission.admit("ctx", GATEWAY, "bob")
    for task_id, owner in (("t1", "alice"), ("t2", "bob")):
        task = Task(
            id=task_id, context_id="ctx", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[versioned("spec", task_id[1:])],
        )
        await setup.store.save(task, ServerCallContext(user=_User(owner)))

    versions = await setup.ledger.versions("ctx", "spec", GATEWAY)

    assert [item.task_id for item in versions] == ["t2", "t1"]


async def test_a_context_being_deleted_has_no_versions() -> None:
    setup = Setup()
    await setup.write("t1", versioned("spec", "0"))
    setup.admission.entries["ctx"].state = DELETING

    assert await setup.ledger.versions("ctx", "spec", ALICE) == []


def test_a_version_is_read_from_the_metadata_however_it_is_written() -> None:
    assert artifact_version(versioned("spec", "7")) == 7
    assert artifact_version(versioned("spec", 7)) == 7
    assert artifact_version(versioned("spec", "latest")) == 0
    assert artifact_version(Artifact(artifact_id="spec")) == 0

