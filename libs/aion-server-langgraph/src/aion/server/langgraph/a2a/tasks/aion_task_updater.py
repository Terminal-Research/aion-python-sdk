import uuid
from typing import Any
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Artifact, TaskArtifactUpdateEvent, Part


class AionTaskUpdater(TaskUpdater):

    # TODO (remove): this corrects a bug in TaskUpdater where it wasn't taking the append 
    # and last_chunk args
    def add_artifact(
        self,
        parts: list[Part],
        artifact_id: str = str(uuid.uuid4()),
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        append: bool = False,
        last_chunk: bool = False,
    ):
        """Adds an artifact chunk to the task and publishes a `TaskArtifactUpdateEvent`.

        Args:
            parts: A list of `Part` objects forming the artifact chunk.
            artifact_id: The ID of the artifact. A new UUID is generated if not provided.
            name: Optional name for the artifact.
            metadata: Optional metadata for the artifact.
            append: Optional boolean indicating if this chunk appends to a previous one.
            last_chunk: Optional boolean indicating if this is the last chunk.
        """
        self.event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                taskId=self.task_id,
                contextId=self.context_id,
                artifact=Artifact(
                    artifactId=artifact_id,
                    name=name,
                    parts=parts,
                    metadata=metadata,
                ),
                append=append,
                last_chunk=last_chunk,
            )
        )
