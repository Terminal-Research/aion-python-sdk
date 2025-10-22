from typing import Any, Optional, Literal
from enum import Enum

from a2a.types import TaskArtifactUpdateEvent, Artifact, Part, TextPart, Task
from aion.shared.types import ArtifactName, ArtifactStreamingStatus, ArtifactStreamingStatusReason
from langchain_core.messages import BaseMessage, AIMessageChunk, AIMessage


class StreamingArtifactBuilderPartMode(Enum):
    """Enumeration for part handling modes.

    CONCATENATED: Accumulate all content in a single Part
    SEPARATED: Add each chunk as a separate Part
    """
    CONCATENATED = "concatenated"
    SEPARATED = "separated"


class StreamingArtifactBuilder:
    """Builder for creating TaskArtifactUpdateEvent objects for streaming responses.

    This builder provides functionality to create and manage streaming artifacts
    with different content accumulation strategies.
    """

    def __init__(
            self,
            task: Task,
            part_mode: StreamingArtifactBuilderPartMode = StreamingArtifactBuilderPartMode.SEPARATED
    ):
        """Initialize the builder with a Task object and part handling mode.

        Args:
            task: The Task object this artifact belongs to
            part_mode: How to handle parts - accumulate in single part or create multiple parts
        """
        self.task = task
        self.task_id = task.id
        self.context_id = task.context_id
        self.artifact_id = ArtifactName.STREAM_DELTA.value
        self.artifact_name = ArtifactName.STREAM_DELTA.value
        self.part_mode = part_mode

    def get_existing_streaming_artifact(self, active_only: bool = True) -> Optional[Artifact]:
        """Get existing streaming artifact if it exists in the task.

        Args:
            active_only: If True, returns only artifacts with ACTIVE status.
                        If False, returns any artifact regardless of status.

        Returns:
            Artifact if found (and active if active_only=True), None otherwise.
            When active_only=True and non-active artifact exists, returns None
            to indicate a new artifact should be created.
        """
        if not self.task.artifacts:
            return None

        for artifact in self.task.artifacts:
            if artifact.artifact_id == self.artifact_id:
                if not active_only:
                    return artifact

                # Check if artifact has active status
                if (
                        artifact.metadata and
                        artifact.metadata.get("status") == ArtifactStreamingStatus.ACTIVE.value
                ):
                    return artifact
                # If artifact exists but is not active, we should replace it
                # by returning None to trigger new artifact creation
                elif artifact.metadata and "status" in artifact.metadata:
                    return None
                # If no status metadata exists, consider it active for backward compatibility
                else:
                    return artifact
        return None

    def _extract_content_from_artifact(self, artifact: Optional[Artifact] = None) -> str:
        """Get the current content of the streaming artifact if it exists."""
        if not artifact:
            return ""

        if self.part_mode == StreamingArtifactBuilderPartMode.CONCATENATED:
            first_part = artifact.parts[0]
            if hasattr(first_part.root, 'text'):
                return first_part.root.text
        else:
            content_parts = []
            for part in artifact.parts:
                if hasattr(part.root, 'text'):
                    content_parts.append(part.root.text)
            return ''.join(content_parts)
        return ""

    def build_from_langgraph_message(
            self,
            langgraph_message: BaseMessage,
            metadata: dict[str, Any] | None = None
    ) -> TaskArtifactUpdateEvent | None:
        """Build streaming artifact event from LangGraph message with automatic type detection.

        This method automatically detects message type, determines if this is intermediate
        or final chunk, handles state management based on part_mode, and accumulates
        content according to the selected part mode.

        Args:
            langgraph_message: LangGraph message object
            metadata: Optional metadata for the artifact

        Returns:
            TaskArtifactUpdateEvent or None if message type is not supported
        """
        if not isinstance(metadata, dict):
            metadata = {}

        if isinstance(langgraph_message, AIMessageChunk):
            return self.build_streaming_chunk_event(
                content=langgraph_message.content,
                metadata=metadata | {
                    "status": ArtifactStreamingStatus.ACTIVE,
                    "status_reason": ArtifactStreamingStatusReason.CHUNK_STREAMING,
                }
            )

        elif isinstance(langgraph_message, AIMessage):
            if not self.get_existing_streaming_artifact():
                return None

            return self.build_finalized_event(
                metadata=metadata | {
                    "status": ArtifactStreamingStatus.FINALIZED,
                    "status_reason": ArtifactStreamingStatusReason.COMPLETE_MESSAGE
                }
            )

        return None

    def build_meta_complete_event(
            self,
            status_reason: ArtifactStreamingStatusReason = ArtifactStreamingStatusReason.COMPLETE_MESSAGE
    ) -> TaskArtifactUpdateEvent | None:
        """Build a meta completion event that finalizes existing artifact without changing content."""
        streaming_artifact = self.get_existing_streaming_artifact()
        if not streaming_artifact:
            return None

        return self._build_chunk_event(
            parts=streaming_artifact.parts,
            append=False,
            last_chunk=False,
            metadata={
                "status": ArtifactStreamingStatus.FINALIZED.value,
                "status_reason": status_reason.value,
            }
        )

    def build_streaming_chunk_event(
            self,
            content: str,
            append: Optional[bool] = None,
            metadata: dict[str, Any] | None = None
    ) -> TaskArtifactUpdateEvent:
        """Build a streaming chunk event with content handling based on part_mode.

        Handles content differently based on part_mode:
        - CONCATENATED mode: accumulates content in single Part
        - SEPARATED mode: adds each chunk as separate Part

        Args:
            content: The text content for this chunk
            append: Optional override for append behavior
            metadata: Optional metadata for the artifact

        Returns:
            Configured TaskArtifactUpdateEvent with proper content handling
        """
        streaming_artifact = self.get_existing_streaming_artifact()
        is_concatenated = self.part_mode == StreamingArtifactBuilderPartMode.CONCATENATED

        # Determine append flag
        if append is None:
            append = not is_concatenated and streaming_artifact is not None

        # Determine content - accumulate for concatenated mode
        if is_concatenated and streaming_artifact:
            existing_content = self._extract_content_from_artifact(streaming_artifact)
            final_content = existing_content + content
        else:
            final_content = content

        return self._build_chunk_event(
            content=final_content,
            append=append,
            last_chunk=False,
            metadata=metadata
        )

    def build_finalized_event(
            self,
            metadata: dict[str, Any] | None = None
    ) -> TaskArtifactUpdateEvent | None:
        """Build a finalization event that keeps existing content as-is.

        Args:
            metadata: Optional metadata for the artifact

        Returns:
            TaskArtifactUpdateEvent or None if no existing artifact found
        """
        streaming_artifact = self.get_existing_streaming_artifact()
        if not streaming_artifact:
            return None

        return self._build_chunk_event(
            parts=streaming_artifact.parts,
            append=False,
            last_chunk=False,
            metadata=metadata
        )

    def _build_chunk_event(
            self,
            content: Optional[str] = None,
            parts: Optional[list[Part]] = None,
            append: bool = False,
            last_chunk: bool = False,
            metadata: dict[str, Any] | None = None
    ) -> TaskArtifactUpdateEvent:
        """Build a TaskArtifactUpdateEvent with the specified parameters.

        Args:
            content: Text content for the event (mutually exclusive with parts)
            parts: List of parts for the event (mutually exclusive with content)
            append: Whether to append to existing parts or replace them
            last_chunk: Whether this is the final chunk
            metadata: Optional metadata for the artifact

        Returns:
            Configured TaskArtifactUpdateEvent
        """
        if content is not None:
            parts = [Part(root=TextPart(text=content))]
        elif parts is None:
            parts = []

        return TaskArtifactUpdateEvent(
            task_id=self.task_id,
            context_id=self.context_id,
            artifact=Artifact(
                artifact_id=self.artifact_id,
                name=self.artifact_name,
                parts=parts,
                metadata=metadata,
            ),
            append=append,
            last_chunk=last_chunk,
        )
