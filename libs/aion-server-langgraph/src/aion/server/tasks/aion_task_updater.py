import uuid
from typing import Any
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Part, TaskStatusUpdateEvent, TaskStatus, Message


class AionTaskUpdater(TaskUpdater):

    # TODO (remove): this corrects a bug in TaskUpdater where it wasn't taking the append 
    # and last_chunk args
    def add_artifact(
        self,
        parts: list[Part],
        metadata: dict[str, Any] | None = None,
        append: bool = False,
        last_chunk: bool = False,
    ):
        """Adds an artifact chunk to the task and publishes a `TaskArtifactUpdateEvent`.

        Args:
            parts: A list of `Part` objects forming the chunk message.
            name: Optional name for the artifact.
            metadata: Optional metadata for the artifact.
            append: Optional boolean indicating if this chunk appends to a previous one.
            last_chunk: Optional boolean indicating if this is the last chunk.
        """
        self.event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=self.task_id,
                contextId=self.context_id,
                status=TaskStatus(message=Message(parts=parts,
                                                  metadata=metadata)),
                append=append,
                last_chunk=last_chunk,
            )
        )

