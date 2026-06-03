"""Tests for A2ATaskDeduplicator.

Focus areas:
  - Platform metadata protection (https://docs.aion.to* keys must not be overwritten)
  - Message deduplication: by ID, without ID, apply_processed_item side-effects
  - Artifact deduplication and transient artifact handling
  - Status event: duplicate detection, state change bypasses deduplication
  - Task merge: original identity is preserved, new history/artifacts are appended
"""

import pytest
from a2a.types import (
    Artifact,
    Message,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct

from aion.server.tasks import deduplicator as deduplicator_module
from aion.server.tasks.deduplicator import A2ATaskDeduplicator
from aion.core.a2a.enums import ArtifactId


PLATFORM_KEY = "https://docs.aion.to/some-platform-key"
USER_KEY = "user-key"


def _make_task(
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    state: TaskState = TaskState.TASK_STATE_WORKING,
    history: list[Message] | None = None,
    artifacts: list[Artifact] | None = None,
    metadata: dict | None = None,
) -> Task:
    task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state),
    )
    if history:
        task.history.extend(history)
    if artifacts:
        task.artifacts.extend(artifacts)
    if metadata:
        s = Struct()
        ParseDict(metadata, s)
        task.metadata.CopyFrom(s)
    return task


def _make_message(message_id: str = "", task_id: str = "task-1", context_id: str = "ctx-1") -> Message:
    return Message(message_id=message_id, role=Role.ROLE_USER, task_id=task_id, context_id=context_id)


def _make_artifact(artifact_id: str) -> Artifact:
    return Artifact(artifact_id=artifact_id)


def _make_status_event(
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    state: TaskState = TaskState.TASK_STATE_WORKING,
    message: Message | None = None,
) -> TaskStatusUpdateEvent:
    event = TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state),
    )
    if message:
        event.status.message.CopyFrom(message)
    return event


def _make_artifact_event(
    artifact_id: str,
    task_id: str = "task-1",
    context_id: str = "ctx-1",
) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        artifact=_make_artifact(artifact_id),
    )


class TestDeduplicateDispatch:
    def test_dispatches_task_payload(self):
        """Verify that the public dispatcher handles Task payloads."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canonical", context_id="ctx"))
        patch = _make_task(task_id="foreign", context_id="other")

        result = ded.deduplicate(patch)

        assert isinstance(result, Task)
        assert result.id == "canonical"
        assert result.context_id == "ctx"

    def test_dispatches_message_payload(self):
        """Verify that the public dispatcher handles Message payloads."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canonical", context_id="ctx"))

        result = ded.deduplicate(_make_message("msg-1", task_id="foreign", context_id="other"))

        assert isinstance(result, Message)
        assert result.task_id == "canonical"
        assert result.context_id == "ctx"

    def test_dispatches_status_event_payload(self):
        """Verify that the public dispatcher handles TaskStatusUpdateEvent payloads."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canonical", context_id="ctx"))

        result = ded.deduplicate(_make_status_event(
            task_id="foreign",
            context_id="other",
            state=TaskState.TASK_STATE_COMPLETED,
        ))

        assert isinstance(result, TaskStatusUpdateEvent)
        assert result.task_id == "canonical"
        assert result.context_id == "ctx"

    def test_dispatches_artifact_event_payload(self):
        """Verify that the public dispatcher handles TaskArtifactUpdateEvent payloads."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canonical", context_id="ctx"))

        result = ded.deduplicate(_make_artifact_event(
            "artifact-1",
            task_id="foreign",
            context_id="other",
        ))

        assert isinstance(result, TaskArtifactUpdateEvent)
        assert result.task_id == "canonical"
        assert result.context_id == "ctx"

    def test_unsupported_type_raises_type_error(self):
        """Verify that unsupported type raises type error."""
        ded = A2ATaskDeduplicator(_make_task())
        with pytest.raises(TypeError):
            ded.deduplicate("not-an-a2a-type")  # type: ignore[arg-type]


class TestDeduplicateMessage:
    def test_new_message_passes_through(self):
        """Verify that new message passes through."""
        ded = A2ATaskDeduplicator(_make_task())
        msg = _make_message("new-msg")
        result = ded.deduplicate_message(msg)
        assert result is not None
        assert result.message_id == "new-msg"

    def test_duplicate_message_id_returns_none(self):
        """Verify that duplicate message ID returns none."""
        original = _make_task(history=[_make_message("existing-msg")])
        ded = A2ATaskDeduplicator(original)
        result = ded.deduplicate_message(_make_message("existing-msg"))
        assert result is None

    def test_message_without_id_is_always_unique(self):
        """Verify that message without ID is always unique."""
        original = _make_task(history=[_make_message("")])
        ded = A2ATaskDeduplicator(original)
        # anonymous messages cannot be deduplicated by ID
        result = ded.deduplicate_message(_make_message(""))
        assert result is not None

    def test_message_task_and_context_ids_are_normalized(self):
        """Verify that message task and context IDs are normalized."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canonical-task", context_id="canonical-ctx"))
        msg = _make_message("msg-1", task_id="other-task", context_id="other-ctx")
        result = ded.deduplicate_message(msg)
        assert result.task_id == "canonical-task"
        assert result.context_id == "canonical-ctx"

    def test_original_message_not_mutated(self):
        """Verify that original message not mutated."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canonical"))
        original_msg = _make_message("msg-1", task_id="foreign")
        ded.deduplicate_message(original_msg)
        assert original_msg.task_id == "foreign"  # must not be mutated

    def test_platform_metadata_stripped_from_message(self):
        """Verify that platform metadata stripped from message."""
        ded = A2ATaskDeduplicator(_make_task())
        msg = _make_message("msg-1")
        s = Struct()
        ParseDict({PLATFORM_KEY: "secret", USER_KEY: "visible"}, s)
        msg.metadata.CopyFrom(s)

        result = ded.deduplicate_message(msg)
        assert result is not None
        d = dict(result.metadata.fields)
        assert PLATFORM_KEY not in d
        assert USER_KEY in d

    def test_platform_only_metadata_clears_message_metadata(self):
        """Verify that metadata is cleared when only platform keys remain."""
        ded = A2ATaskDeduplicator(_make_task())
        msg = _make_message("msg-1")
        s = Struct()
        ParseDict({PLATFORM_KEY: "secret"}, s)
        msg.metadata.CopyFrom(s)

        result = ded.deduplicate_message(msg)

        assert result is not None
        assert not result.HasField("metadata")

    def test_reference_task_ids_are_normalized_and_deduplicated(self):
        """Verify that references include the canonical task once."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canonical"))
        msg = _make_message("msg-1")
        msg.reference_task_ids.extend(["", "canonical", "other", "other"])

        result = ded.deduplicate_message(msg)

        assert result is not None
        assert list(result.reference_task_ids) == ["canonical", "other"]

    def test_apply_processed_item_message_updates_known_ids(self):
        """Verify that apply processed item message updates known IDs."""
        ded = A2ATaskDeduplicator(_make_task())
        msg = _make_message("msg-new")
        ded.apply_processed_item(msg)
        # After apply, the same message should be a duplicate
        assert ded.deduplicate_message(_make_message("msg-new")) is None


class TestDeduplicateArtifactEvent:
    def test_new_artifact_passes_through(self):
        """Verify that new artifact passes through."""
        ded = A2ATaskDeduplicator(_make_task())
        event = _make_artifact_event("art-1")
        assert ded.deduplicate_artifact_event(event) is not None

    def test_known_artifact_returns_none(self):
        """Verify that known artifact returns none."""
        original = _make_task(artifacts=[_make_artifact("art-1")])
        ded = A2ATaskDeduplicator(original)
        assert ded.deduplicate_artifact_event(_make_artifact_event("art-1")) is None

    def test_artifact_without_id_always_passes(self):
        """Verify that artifact without ID always passes."""
        original = _make_task(artifacts=[_make_artifact("")])
        ded = A2ATaskDeduplicator(original)
        event = _make_artifact_event("")
        assert ded.deduplicate_artifact_event(event) is not None

    def test_apply_processed_item_non_transient_artifact_tracked(self):
        """Verify that apply processed item non transient artifact tracked."""
        ded = A2ATaskDeduplicator(_make_task())
        event = _make_artifact_event("persistent-art")
        ded.apply_processed_item(event)
        assert ded.deduplicate_artifact_event(_make_artifact_event("persistent-art")) is None

    def test_apply_processed_item_transient_artifact_not_tracked(self):
        """Transient artifacts (stream-delta, ephemeral-message) must not pollute known IDs."""
        ded = A2ATaskDeduplicator(_make_task())
        for transient_id in (ArtifactId.STREAM_DELTA.value, ArtifactId.EPHEMERAL_MESSAGE.value):
            event = _make_artifact_event(transient_id)
            ded.apply_processed_item(event)
            # Still passes through — transient IDs are never deduplicated
            assert ded.deduplicate_artifact_event(_make_artifact_event(transient_id)) is not None


class TestDeduplicateStatusEvent:
    def test_same_state_no_message_no_metadata_is_duplicate(self):
        """Verify that same state no message no metadata is duplicate."""
        original = _make_task(state=TaskState.TASK_STATE_WORKING)
        ded = A2ATaskDeduplicator(original)
        event = _make_status_event(state=TaskState.TASK_STATE_WORKING)
        assert ded.deduplicate_status_event(event) is None

    def test_state_change_passes_through(self):
        """Verify that state change passes through."""
        original = _make_task(state=TaskState.TASK_STATE_WORKING)
        ded = A2ATaskDeduplicator(original)
        event = _make_status_event(state=TaskState.TASK_STATE_COMPLETED)
        assert ded.deduplicate_status_event(event) is not None

    def test_same_state_with_new_message_passes_through(self):
        """Verify that same state with new message passes through."""
        original = _make_task(state=TaskState.TASK_STATE_WORKING)
        ded = A2ATaskDeduplicator(original)
        event = _make_status_event(
            state=TaskState.TASK_STATE_WORKING,
            message=_make_message("new-msg"),
        )
        result = ded.deduplicate_status_event(event)
        assert result is not None

    def test_same_state_with_duplicate_message_removes_message_and_deduplicates(self):
        """Status event carrying only an already-known message should be dropped."""
        original = _make_task(
            state=TaskState.TASK_STATE_WORKING,
            history=[_make_message("known-msg")],
        )
        ded = A2ATaskDeduplicator(original)
        event = _make_status_event(
            state=TaskState.TASK_STATE_WORKING,
            message=_make_message("known-msg"),
        )
        # The message is stripped; the event reduces to same-state + no message = duplicate
        assert ded.deduplicate_status_event(event) is None

    def test_ids_are_normalized_in_status_event(self):
        """Verify that IDs are normalized in status event."""
        ded = A2ATaskDeduplicator(_make_task(task_id="canon", context_id="canon-ctx"))
        event = _make_status_event(
            task_id="foreign",
            context_id="foreign-ctx",
            state=TaskState.TASK_STATE_COMPLETED,
        )
        result = ded.deduplicate_status_event(event)
        assert result.task_id == "canon"
        assert result.context_id == "canon-ctx"

    def test_same_state_with_metadata_passes_through(self):
        """Verify that metadata makes a same-state status event meaningful."""
        ded = A2ATaskDeduplicator(_make_task(state=TaskState.TASK_STATE_WORKING))
        event = _make_status_event(state=TaskState.TASK_STATE_WORKING)
        s = Struct()
        ParseDict({USER_KEY: "value"}, s)
        event.metadata.CopyFrom(s)

        result = ded.deduplicate_status_event(event)

        assert result is not None
        assert MessageToDict(result.metadata, preserving_proto_field_name=True) == {USER_KEY: "value"}

    def test_no_original_status_makes_event_non_duplicate(self):
        """Verify that an original task without explicit status does not drop status events."""
        original = Task(id="task-1", context_id="ctx-1")
        ded = A2ATaskDeduplicator(original)

        result = ded.deduplicate_status_event(_make_status_event())

        assert result is not None

    def test_apply_processed_item_status_event_updates_status_and_known_message_ids(self):
        """Verify that applying a status event updates cached status and message IDs."""
        ded = A2ATaskDeduplicator(_make_task(state=TaskState.TASK_STATE_WORKING))
        event = _make_status_event(
            state=TaskState.TASK_STATE_COMPLETED,
            message=_make_message("status-msg"),
        )

        ded.apply_processed_item(event)

        assert ded.deduplicate_message(_make_message("status-msg")) is None
        assert ded.deduplicate_status_event(_make_status_event(
            state=TaskState.TASK_STATE_COMPLETED,
        )) is None


class TestDeduplicateTask:
    def test_original_task_identity_is_preserved(self):
        """Verify that original task identity is preserved."""
        original = _make_task(task_id="original-id", context_id="original-ctx")
        ded = A2ATaskDeduplicator(original)
        # Patch task tries to override identity fields
        patch = _make_task(task_id="foreign-id", context_id="foreign-ctx")
        result = ded.deduplicate_task(patch)
        assert result.id == "original-id"
        assert result.context_id == "original-ctx"

    def test_new_history_messages_are_appended(self):
        """Verify that new history messages are appended."""
        original = _make_task(history=[_make_message("msg-1")])
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(history=[_make_message("msg-1"), _make_message("msg-2")])
        result = ded.deduplicate_task(patch)
        ids = {m.message_id for m in result.history}
        assert "msg-1" in ids
        assert "msg-2" in ids

    def test_duplicate_history_messages_not_duplicated(self):
        """Verify that duplicate history messages not duplicated."""
        original = _make_task(history=[_make_message("msg-1")])
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(history=[_make_message("msg-1")])
        result = ded.deduplicate_task(patch)
        # msg-1 appears exactly once
        count = sum(1 for m in result.history if m.message_id == "msg-1")
        assert count == 1

    def test_new_artifact_is_appended(self):
        """Verify that new artifact is appended."""
        original = _make_task(artifacts=[_make_artifact("art-1")])
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(artifacts=[_make_artifact("art-1"), _make_artifact("art-2")])
        result = ded.deduplicate_task(patch)
        ids = {a.artifact_id for a in result.artifacts}
        assert "art-1" in ids
        assert "art-2" in ids

    def test_artifact_without_id_is_appended_in_task_merge(self):
        """Verify that task merge keeps artifacts that cannot be deduplicated by ID."""
        ded = A2ATaskDeduplicator(_make_task())
        patch = _make_task(artifacts=[_make_artifact("")])

        result = ded.deduplicate_task(patch)

        assert len(result.artifacts) == 1
        assert result.artifacts[0].artifact_id == ""

    def test_new_status_message_is_preserved_in_task_merge(self):
        """Verify that a new status message in a task patch survives normalization."""
        ded = A2ATaskDeduplicator(_make_task())
        patch = _make_task(state=TaskState.TASK_STATE_COMPLETED)
        patch.status.message.CopyFrom(_make_message("new-status-msg"))

        result = ded.deduplicate_task(patch)

        assert result.status.HasField("message")
        assert result.status.message.message_id == "new-status-msg"

    def test_duplicate_status_message_is_removed_in_task_merge(self):
        """Verify that duplicate status messages are stripped from task patches."""
        original = _make_task(history=[_make_message("known-status-msg")])
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(state=TaskState.TASK_STATE_COMPLETED)
        patch.status.message.CopyFrom(_make_message("known-status-msg"))

        result = ded.deduplicate_task(patch)

        assert not result.status.HasField("message")

    def test_original_task_not_mutated(self):
        """Verify that original task not mutated."""
        original = _make_task()
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(history=[_make_message("msg-x")])
        ded.deduplicate_task(patch)
        assert len(original.history) == 0


class TestPlatformMetadataProtection:
    def test_platform_key_not_overwritten_in_task_merge(self):
        """Verify that platform key not overwritten in task merge."""
        original = _make_task(metadata={PLATFORM_KEY: "original-value", USER_KEY: "old"})
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(metadata={PLATFORM_KEY: "hacker-value", USER_KEY: "new"})
        result = ded.deduplicate_task(patch)

        result_metadata = dict(result.metadata.fields)
        # Platform key retains its original value
        assert result_metadata.get(PLATFORM_KEY) is not None
        assert result_metadata[PLATFORM_KEY].string_value == "original-value"
        # User key was updated
        assert result_metadata[USER_KEY].string_value == "new"

    def test_platform_key_not_introduced_by_patch(self):
        """A patch must not introduce new platform keys."""
        original = _make_task()
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(metadata={PLATFORM_KEY: "injected"})
        result = ded.deduplicate_task(patch)

        result_metadata = dict(result.metadata.fields)
        assert PLATFORM_KEY not in result_metadata

    def test_nested_metadata_merges_and_strips_platform_keys_in_lists(self):
        """Verify recursive user metadata merge and nested platform-key stripping."""
        original = _make_task(metadata={
            "nested": {
                "keep": "base",
                "unchanged": "still-here",
            },
        })
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(metadata={
            "nested": {
                "keep": "patched",
            },
            "items": [
                {
                    PLATFORM_KEY: "blocked",
                    "ok": "visible",
                },
            ],
        })

        result = ded.deduplicate_task(patch)
        metadata = MessageToDict(result.metadata, preserving_proto_field_name=True)

        assert metadata["nested"] == {
            "keep": "patched",
            "unchanged": "still-here",
        }
        assert metadata["items"] == [{"ok": "visible"}]

    def test_partial_message_overlap_logs_warning(self, monkeypatch):
        """Verify that a partial message overlap is logged."""
        original = _make_task(history=[_make_message("msg-1"), _make_message("msg-2")])
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(history=[_make_message("msg-1")])
        warnings = []

        monkeypatch.setattr(
            deduplicator_module.logger,
            "warning",
            lambda message, *args: warnings.append(message % args),
        )

        ded.deduplicate_task(patch)

        assert any("Partial canonical overlap detected" in warning for warning in warnings)
        assert any("missed messages: ['msg-2']" in warning for warning in warnings)

    def test_partial_artifact_overlap_logs_warning(self, monkeypatch):
        """Verify that a partial artifact overlap is logged."""
        original = _make_task(artifacts=[_make_artifact("art-1"), _make_artifact("art-2")])
        ded = A2ATaskDeduplicator(original)
        patch = _make_task(artifacts=[_make_artifact("art-1")])
        warnings = []

        monkeypatch.setattr(
            deduplicator_module.logger,
            "warning",
            lambda message, *args: warnings.append(message % args),
        )

        ded.deduplicate_task(patch)

        assert any("Partial canonical overlap detected" in warning for warning in warnings)
        assert any("missed artifacts: ['art-2']" in warning for warning in warnings)

    def test_full_or_additive_overlap_does_not_log_warning(self, monkeypatch):
        """Verify that full snapshots and additive patches do not warn."""
        original = _make_task(
            history=[_make_message("msg-1"), _make_message("msg-2")],
            artifacts=[_make_artifact("art-1"), _make_artifact("art-2")],
        )
        ded = A2ATaskDeduplicator(original)
        warnings = []

        monkeypatch.setattr(
            deduplicator_module.logger,
            "warning",
            lambda message, *args: warnings.append(message % args),
        )

        ded.deduplicate_task(_make_task(
            history=[_make_message("msg-1"), _make_message("msg-2")],
            artifacts=[_make_artifact("art-1"), _make_artifact("art-2")],
        ))
        ded.deduplicate_task(_make_task(
            history=[_make_message("msg-3")],
            artifacts=[_make_artifact("art-3")],
        ))

        assert warnings == []


class TestApplyProcessedItemTask:
    def test_task_replacement_updates_known_ids(self):
        """Verify that task replacement updates known IDs."""
        ded = A2ATaskDeduplicator(_make_task())
        new_task = _make_task(history=[_make_message("msg-a"), _make_message("msg-b")])
        ded.apply_processed_item(new_task)
        # Both messages should now be considered known
        assert ded.deduplicate_message(_make_message("msg-a")) is None
        assert ded.deduplicate_message(_make_message("msg-b")) is None

    def test_apply_does_not_expose_internal_reference(self):
        """apply_processed_item must deep-copy; mutating the passed task afterward
        must not affect the deduplicator's internal state."""
        task = _make_task(history=[_make_message("msg-1")])
        ded = A2ATaskDeduplicator(_make_task())
        ded.apply_processed_item(task)
        # Modify the task we passed in
        task.history.append(_make_message("msg-injected"))
        # The deduplicator should not see the injected message
        assert ded.deduplicate_message(_make_message("msg-injected")) is not None


class TestDefensiveHelpers:
    def test_has_field_returns_false_without_hasfield_method(self):
        """Verify defensive handling for non-protobuf objects."""
        assert A2ATaskDeduplicator._has_field(object(), "status") is False
