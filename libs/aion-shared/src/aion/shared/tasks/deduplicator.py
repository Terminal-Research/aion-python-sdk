"""Deduplication helpers for A2A task-related payloads.

This module provides a stateful deduplicator for normalizing outgoing A2A payloads
against the original task. The deduplicator caches message and artifact IDs for
efficient deduplication across multiple payloads. It keeps the original task identity
authoritative while removing duplicate messages and artifacts from task payloads.

Metadata keys under the ``https://docs.aion.to`` namespace are treated as
platform-owned and are never allowed to be overwritten by incoming payloads.
"""

from __future__ import annotations

import copy
from typing import Any

from a2a.types import Artifact, Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct
from aion.shared.logging import get_logger

__all__ = ["A2ATaskDeduplicator"]

PLATFORM_METADATA_PREFIX = "https://docs.aion.to"
logger = get_logger()


class A2ATaskDeduplicator:
    """Stateful deduplicator for A2A task-related payloads.

    Initialized with a known original task. Caches message and artifact IDs for
    efficient deduplication of multiple payloads. Normalizes any incoming `Task`,
    `Message`, `TaskStatusUpdateEvent`, or `TaskArtifactUpdateEvent` relative to
    the original task and suppresses payloads that are already represented.
    """

    def __init__(self, original_task: Task) -> None:
        """Initialize deduplicator with an original task.

        Args:
            original_task: The authoritative task to normalize against.
                          Message and artifact IDs are cached for efficient lookups.
        """
        self._original_task = original_task
        self._known_message_ids: set[str] = self._collect_known_message_ids(original_task)
        self._known_artifact_ids: set[str] = self._collect_known_artifact_ids(original_task)

    def deduplicate(
        self,
        item: Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None:
        """Deduplicate and normalize an A2A payload.

        Args:
            item: The task, message, or task-related event to deduplicate.

        Returns:
            A normalized payload, or `None` when the payload is already fully
            represented by the original task.

        Raises:
            TypeError: If `item` is not one of the supported A2A payload types.
        """
        if isinstance(item, Task):
            return self.deduplicate_task(item)
        if isinstance(item, Message):
            return self.deduplicate_message(item)
        if isinstance(item, TaskStatusUpdateEvent):
            return self.deduplicate_status_event(item)
        if isinstance(item, TaskArtifactUpdateEvent):
            return self.deduplicate_artifact_event(item)
        raise TypeError(f"Unsupported payload type: {type(item)!r}")

    def apply_processed_item(
        self,
        item: Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        """Apply a successfully processed item to the internal task cache.

        Updates only the internal cached state. Does not modify the passed item or
        affect external state. Call this after an item is enqueued to keep the
        deduplicator's view of the task in sync, avoiding unnecessary database queries.

        IMPORTANT: Must be called AFTER item is enqueued successfully.
        Calling before enqueue can cause inconsistent deduplication state.

        Args:
            item: The item that was successfully processed and enqueued.
        """
        if isinstance(item, Task):
            self._original_task = copy.deepcopy(item)
            self._known_message_ids = self._collect_known_message_ids(self._original_task)
            self._known_artifact_ids = self._collect_known_artifact_ids(self._original_task)

        elif isinstance(item, Message):
            self._original_task.history.append(copy.deepcopy(item))
            if item.message_id:
                self._known_message_ids.add(item.message_id)

        elif isinstance(item, TaskStatusUpdateEvent):
            self._original_task.status.CopyFrom(copy.deepcopy(item.status))
            if self._has_field(item.status, "message") and item.status.message.message_id:
                self._known_message_ids.add(item.status.message.message_id)

        elif isinstance(item, TaskArtifactUpdateEvent):
            self._original_task.artifacts.append(copy.deepcopy(item.artifact))
            if item.artifact.artifact_id:
                self._known_artifact_ids.add(item.artifact.artifact_id)

    def deduplicate_task(self, task: Task) -> Task:
        """Merge a task patch into the original task.

        The original task identity is preserved. Incoming history and artifacts
        are merged into the original task while duplicate messages and artifacts
        are removed.

        Args:
            task: The task payload that should be merged into the original task.

        Returns:
            A new task containing the merged payload.
        """
        self._warn_on_partial_overlap(task)

        merged = copy.deepcopy(self._original_task)
        merged.id = self._original_task.id
        merged.context_id = self._original_task.context_id

        self._merge_task_metadata(merged, task.metadata)

        normalized_status_message: Message | None = None
        if self._has_field(task, "status"):
            normalized_status = copy.deepcopy(task.status)
            self._sanitize_struct_field(normalized_status, "metadata")
            if self._has_field(normalized_status, "message"):
                normalized_status_message = self.deduplicate_message(
                    normalized_status.message,
                )
                if normalized_status_message is None:
                    normalized_status.ClearField("message")
                else:
                    normalized_status.message.CopyFrom(normalized_status_message)
            merged.status.CopyFrom(normalized_status)
        else:
            normalized_status_message = None

        self._merge_messages(
            merged,
            task.history,
            extra_messages=[normalized_status_message] if normalized_status_message else None,
        )
        self._merge_artifacts(merged, task.artifacts)
        return merged

    def deduplicate_message(self, message: Message) -> Message | None:
        """Normalize a message relative to the original task.

        The message is rewritten to reference the original task. Messages are
        deduplicated strictly by `message_id`. If a message does not have an
        identifier, it is always treated as unique.

        Args:
            message: The message to normalize.

        Returns:
            A normalized message, or `None` if the message is a duplicate.
        """
        normalized = copy.deepcopy(message)
        normalized.task_id = self._original_task.id
        normalized.context_id = self._original_task.context_id
        self._normalize_reference_task_ids(normalized, self._original_task.id)
        self._sanitize_struct_field(normalized, "metadata")

        if self._message_is_duplicate(normalized):
            return None

        return normalized

    def deduplicate_status_event(
        self,
        event: TaskStatusUpdateEvent,
    ) -> TaskStatusUpdateEvent | None:
        """Normalize a task status update event.

        Args:
            event: The task status update event to normalize.

        Returns:
            A normalized event, or `None` if the event does not add anything new
            relative to the original task.
        """
        normalized = copy.deepcopy(event)
        normalized.task_id = self._original_task.id
        normalized.context_id = self._original_task.context_id
        self._sanitize_struct_field(normalized, "metadata")

        if self._has_status_message(normalized):
            normalized_message = self.deduplicate_message(
                normalized.status.message,
            )
            if normalized_message is None:
                normalized.status.ClearField("message")
            else:
                normalized.status.message.CopyFrom(normalized_message)

        if self._status_event_is_duplicate(normalized):
            return None
        return normalized

    def deduplicate_artifact_event(
        self,
        event: TaskArtifactUpdateEvent,
    ) -> TaskArtifactUpdateEvent | None:
        """Normalize a task artifact update event.

        Args:
            event: The task artifact update event to normalize.

        Returns:
            A normalized event, or `None` if the artifact is already represented
            by the original task.
        """
        normalized = copy.deepcopy(event)
        normalized.task_id = self._original_task.id
        normalized.context_id = self._original_task.context_id
        self._sanitize_struct_field(normalized, "metadata")

        artifact_id = normalized.artifact.artifact_id
        if artifact_id and artifact_id in self._known_artifact_ids:
            return None

        return normalized

    def _merge_messages(
        self,
        target: Task,
        incoming_messages: list[Message],
        extra_messages: list[Message] | None = None,
    ) -> None:
        """Merge unique messages into a task history."""
        if extra_messages:
            for message in extra_messages:
                target.history.append(message)

        for message in incoming_messages:
            normalized = self.deduplicate_message(message)
            if normalized is not None:
                target.history.append(normalized)

    @classmethod
    def _has_status_message(cls, message_container: Any) -> bool:
        """Return `True` if a message container has an explicit status.message field."""
        return (cls._has_field(message_container, "status") and
                cls._has_field(message_container.status, "message"))

    @classmethod
    def _collect_known_message_ids(cls, task: Task) -> set[str]:
        """Collect all known message IDs from task history and status message."""
        ids = {message.message_id for message in task.history if message.message_id}
        if cls._has_status_message(task):
            if task.status.message.message_id:
                ids.add(task.status.message.message_id)
        return ids

    @classmethod
    def _collect_known_artifact_ids(cls, task: Task) -> set[str]:
        """Collect all known artifact IDs from task artifacts."""
        return {artifact.artifact_id for artifact in task.artifacts if artifact.artifact_id}

    def _warn_on_partial_overlap(self, patch: Task) -> None:
        """Log a warning when a task patch partially overlaps canonical state.

        If the patch contains any previously emitted message or artifact IDs, it
        must contain the full canonical set for that collection. Partial overlap
        suggests an attempt to override only part of the existing task state.
        Purely additive patches and full canonical snapshots do not trigger a
        warning.
        """
        patch_message_ids = {
            message.message_id for message in patch.history if message.message_id
        }
        partial_message_overlap = self._get_partial_overlap_details(
            canonical_ids=self._known_message_ids,
            patch_ids=patch_message_ids,
        )

        patch_artifact_ids = {
            artifact.artifact_id for artifact in patch.artifacts if artifact.artifact_id
        }
        partial_artifact_overlap = self._get_partial_overlap_details(
            canonical_ids=self._known_artifact_ids,
            patch_ids=patch_artifact_ids,
        )

        if partial_message_overlap is None and partial_artifact_overlap is None:
            return

        warning_parts: list[str] = []
        if partial_message_overlap is not None:
            warning_parts.append(
                f"missed messages: {sorted(partial_message_overlap['missing_ids'])}"
            )
        if partial_artifact_overlap is not None:
            warning_parts.append(
                f"missed artifacts: {sorted(partial_artifact_overlap['missing_ids'])}"
            )

        logger.warning(
            "Partial canonical overlap detected in task patch; %s.",
            "; ".join(warning_parts),
        )

    @classmethod
    def _get_partial_overlap_details(
        cls,
        canonical_ids: set[str],
        patch_ids: set[str],
    ) -> dict[str, set[str]] | None:
        """Return overlap details only for partial canonical overlap."""
        if not canonical_ids or not patch_ids:
            return None

        overlap_ids = canonical_ids & patch_ids
        if not overlap_ids:
            return None

        missing_ids = canonical_ids - patch_ids
        if not missing_ids:
            return None

        return {
            "overlap_ids": overlap_ids,
            "missing_ids": missing_ids,
        }


    def _merge_artifacts(self, target: Task, incoming_artifacts: list[Artifact]) -> None:
        """Merge unique artifacts into a task artifact list.

        Artifacts are deduplicated strictly by `artifact_id`. Artifacts without
        an identifier are always treated as unique and appended as-is.
        """
        for artifact in incoming_artifacts:
            normalized = copy.deepcopy(artifact)
            artifact_id = normalized.artifact_id

            if not artifact_id:
                target.artifacts.append(normalized)
                continue

            if artifact_id in self._known_artifact_ids:
                continue

            target.artifacts.append(normalized)

    @classmethod
    def _merge_structs(cls, base: Struct, patch: Struct) -> Struct:
        """Merge two protobuf structs while preserving platform-owned keys."""
        merged = cls._protobuf_to_dict(base)
        cls._merge_metadata_dicts(
            merged,
            cls._protobuf_to_dict(patch),
        )

        result = Struct()
        if merged:
            ParseDict(merged, result)
        return result

    def _merge_task_metadata(self, target: Task, patch: Struct) -> None:
        """Merge task metadata while protecting platform-owned keys."""
        if not self._has_field(target, "metadata"):
            target.metadata.CopyFrom(Struct())

        merged = self._merge_structs(target.metadata, patch)
        target.metadata.CopyFrom(merged)

    @classmethod
    def _merge_metadata_dicts(cls, target: dict[str, Any], source: dict[str, Any]) -> None:
        """Merge source metadata into target without allowing platform key overrides."""
        for key, value in source.items():
            if cls._is_platform_metadata_key(key):
                continue

            existing = target.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                cls._merge_metadata_dicts(existing, value)
                continue

            target[key] = cls._strip_platform_metadata_keys(value)

    @classmethod
    def _strip_platform_metadata_keys(cls, value: Any) -> Any:
        """Remove platform-owned metadata keys from a nested metadata value."""
        if isinstance(value, dict):
            return {
                key: cls._strip_platform_metadata_keys(inner_value)
                for key, inner_value in value.items()
                if not cls._is_platform_metadata_key(key)
            }
        if isinstance(value, list):
            return [cls._strip_platform_metadata_keys(item) for item in value]
        return value

    @classmethod
    def _sanitize_struct_field(cls, message: Any, field_name: str) -> None:
        """Strip platform-owned metadata keys from a protobuf Struct field."""
        if not cls._has_field(message, field_name):
            return

        struct_value = getattr(message, field_name)
        sanitized = cls._strip_platform_metadata_keys(
            cls._protobuf_to_dict(struct_value)
        )

        if not sanitized:
            message.ClearField(field_name)
            return

        new_struct = Struct()
        ParseDict(sanitized, new_struct)
        getattr(message, field_name).CopyFrom(new_struct)

    @classmethod
    def _normalize_reference_task_ids(cls, message: Message, original_task_id: str) -> None:
        """Ensure the original task ID is present once in the reference list."""
        reference_task_ids: list[str] = [original_task_id]
        reference_task_ids.extend(message.reference_task_ids)

        deduplicated: list[str] = []
        seen: set[str] = set()
        for task_id in reference_task_ids:
            if not task_id or task_id in seen:
                continue
            deduplicated.append(task_id)
            seen.add(task_id)

        del message.reference_task_ids[:]
        message.reference_task_ids.extend(deduplicated)

    def _message_is_duplicate(self, message: Message) -> bool:
        """Return `True` when a message ID is already represented by the task."""
        message_id = message.message_id
        if not message_id:
            return False
        return message_id in self._known_message_ids

    def _status_event_is_duplicate(self, event: TaskStatusUpdateEvent) -> bool:
        """Return `True` when a status event adds no new information."""
        if not self._has_field(self._original_task, "status"):
            return False

        if event.status.state != self._original_task.status.state:
            return False

        if self._protobuf_to_dict(event.metadata):
            return False

        if not self._has_field(event.status, "message"):
            return True

        return self._message_is_duplicate(event.status.message)

    @classmethod
    def _protobuf_to_dict(cls, value: Any) -> dict[str, Any]:
        """Convert a protobuf message into a dictionary for comparison."""
        return MessageToDict(
            value,
            preserving_proto_field_name=True,
            use_integers_for_enums=True,
        )

    @classmethod
    def _has_field(cls, message: Any, field_name: str) -> bool:
        """Return `True` if a protobuf field is explicitly populated."""
        has_field = getattr(message, "HasField", None)
        if has_field is None:
            return False

        try:
            return has_field(field_name)
        except ValueError:
            return False

    @classmethod
    def _is_platform_metadata_key(cls, key: str) -> bool:
        """Return `True` for reserved platform metadata keys."""
        return isinstance(key, str) and key.startswith(PLATFORM_METADATA_PREFIX)
