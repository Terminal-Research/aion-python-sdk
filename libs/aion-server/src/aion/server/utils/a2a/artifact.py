from typing import Any, Optional, TYPE_CHECKING

from a2a.types import TaskArtifactUpdateEvent, Artifact, Part, TextPart, Task
from aion.shared.types import ArtifactName, ArtifactId

if TYPE_CHECKING:
    from aion.shared.agent.adapters import MessageEvent


class StreamingArtifactBuilder:
    """Builder for creating TaskArtifactUpdateEvent objects for streaming responses.

    Creates streaming artifacts where each chunk is added as a separate Part.
    Stream delta artifacts are ephemeral - they are sent to the client for display
    but not persisted in the task's artifact history.
    """

    def __init__(self, task: Task):
        """Initialize the builder with a Task object.

        Args:
            task: The Task object this artifact belongs to
        """
        self.task = task
        self.task_id = task.id
        self.context_id = task.context_id
        self.artifact_id = ArtifactId.STREAM_DELTA.value
        self.artifact_name = ArtifactName.STREAM_DELTA.value
        self._streaming_started = False


    def build_streaming_chunk_event(
            self,
            message_event: "MessageEvent",
            metadata: dict[str, Any] | None = None
    ) -> TaskArtifactUpdateEvent:
        """Build a streaming chunk event.

        Each chunk is added as a separate Part. First chunk creates a new artifact,
        subsequent chunks append to it.

        Args:
            message_event: MessageEvent containing chunk data and flags (is_chunk, is_last_chunk)
            metadata: Optional metadata for the artifact

        Returns:
            Configured TaskArtifactUpdateEvent
        """
        # Extract content from message event
        content = message_event.get_text_content()

        # First chunk: append=False, subsequent chunks: append=True
        append = self._streaming_started

        # Mark that streaming has started
        if not self._streaming_started:
            self._streaming_started = True

        return self._build_chunk_event(
            content=content,
            append=append,
            last_chunk=message_event.is_last_chunk,
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
