"""Builder class for creating streaming artifacts in A2A tasks."""

from typing import Any
from enum import Enum

from a2a.types import TaskArtifactUpdateEvent, Artifact, Part, TextPart, Task
from aion.server.types import ArtifactName
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
            part_mode: StreamingArtifactBuilderPartMode = StreamingArtifactBuilderPartMode.CONCATENATED
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

    def has_existing_streaming_artifact(self) -> bool:
        """Check if a streaming artifact already exists in the task."""
        if not self.task.artifacts:
            return False

        return any(
            artifact.artifact_id == self.artifact_id
            for artifact in self.task.artifacts
        )

    def get_existing_streaming_content(self) -> str:
        """Get the current content of the streaming artifact if it exists."""
        if not self.task.artifacts:
            return ""

        for artifact in self.task.artifacts:
            if artifact.artifact_id == self.artifact_id and artifact.parts:
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

        return ""

    def build_from_langgraph_message(
            self,
            langgraph_message: BaseMessage,
            metadata: dict[str, Any] | None = None,
            skip_final_if_no_chunk: bool = True
    ) -> TaskArtifactUpdateEvent | None:
        """Build streaming artifact event from LangGraph message with automatic type detection.

        This method automatically detects message type, determines if this is intermediate
        or final chunk, handles state management based on part_mode, and accumulates
        content according to the selected part mode.

        Args:
            langgraph_message: LangGraph message object
            metadata: Optional metadata for the artifact
            skip_final_if_no_chunk: Skip final event if no streaming artifact exists

        Returns:
            TaskArtifactUpdateEvent or None if message type is not supported
        """
        if isinstance(langgraph_message, AIMessageChunk):
            return self.build_streaming_chunk_event(
                content=langgraph_message.content,
                is_final=False,
                metadata=metadata
            )

        elif isinstance(langgraph_message, AIMessage):
            if skip_final_if_no_chunk and not self.has_existing_streaming_artifact():
                return None

            return self.build_streaming_chunk_event(
                content=langgraph_message.content,
                is_final=True,
                metadata=metadata
            )

        return None

    def build_streaming_chunk_event(
            self,
            content: str,
            is_final: bool = False,
            metadata: dict[str, Any] | None = None
    ) -> TaskArtifactUpdateEvent:
        """Build a streaming chunk event with content handling based on part_mode.

        Handles content differently based on part_mode:
        - CONCATENATED mode: accumulates content in single Part
        - SEPARATED mode: adds each chunk as separate Part

        Args:
            content: The text content for this chunk
            is_final: Whether this is the final chunk of the stream
            metadata: Optional metadata for the artifact

        Returns:
            Configured TaskArtifactUpdateEvent with proper content handling
        """
        has_existing = self.has_existing_streaming_artifact()
        is_concatenated = self.part_mode == StreamingArtifactBuilderPartMode.CONCATENATED

        # Determine append flag
        append = not is_concatenated and has_existing

        # Determine content
        if is_concatenated and has_existing and not is_final:
            # Accumulate content for concatenated mode on intermediate chunks
            existing_content = self.get_existing_streaming_content()
            final_content = existing_content + content
        else:
            # Use content as-is for all other cases
            final_content = content

        return self._build_chunk_event(
            content=final_content,
            append=append,
            last_chunk=is_final,
            metadata=metadata
        )

    def _build_chunk_event(
            self,
            content: str,
            append: bool = False,
            last_chunk: bool = False,
            metadata: dict[str, Any] | None = None
    ) -> TaskArtifactUpdateEvent:
        """Build a TaskArtifactUpdateEvent with the specified parameters.

        Args:
            content: Text content for the event
            append: Whether to append to existing parts or replace them
            last_chunk: Whether this is the final chunk
            metadata: Optional metadata for the artifact

        Returns:
            Configured TaskArtifactUpdateEvent
        """
        parts = [Part(root=TextPart(text=content))]
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
