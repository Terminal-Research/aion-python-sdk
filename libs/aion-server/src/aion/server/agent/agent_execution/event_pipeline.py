import copy
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Task, TaskArtifactUpdateEvent
from aion.shared.agent.execution.scope import AgentExecutionScopeHelper
from aion.shared.files.a2a import A2AFileTransformer
from aion.shared.logging import get_logger
from aion.shared.tasks import A2ATaskDeduplicator
from typing import Optional

logger = get_logger()


class AionEventPipeline:
    """Orchestrates per-event processing: first-event trigger, transforms, routing."""

    def __init__(
            self,
            event_queue: EventQueue,
            task_updater: TaskUpdater,
            file_transformer: Optional[A2AFileTransformer] = None,
    ):
        self._queue = event_queue
        self._task_updater = task_updater
        self._file_transformer = file_transformer
        self._first_event = True
        self._deduplicator: Optional[A2ATaskDeduplicator] = None

    @property
    def _task_manager(self):
        return AgentExecutionScopeHelper.get_task_manager()

    async def process(self, event) -> None:
        await self._ensure_task_started()
        event = await self._prepare_event(event)
        event = await self._deduplicate_event(event)
        if event is None:
            return

        # Task and Message events are persisted silently (not streamed to client)
        if isinstance(event, (Task, Message)):
            await self._save_silently(event)
        else:
            # All other events are streamed to client
            await self._emit_to_client(event)

        if self._deduplicator is not None:
            self._deduplicator.apply_processed_item(event)

    async def _save_silently(self, event) -> None:
        """Persist event to database without emitting to client.

        Task and Message events bypass the event queue and are saved
        directly to the database without being streamed to the client.
        """
        task_manager = self._task_manager
        if task_manager:
            await task_manager.process(event)
        else:
            logger.warning("Cannot process event silently: task_manager is not initialized.")

    async def _emit_to_client(self, event) -> None:
        """Emit event to client via event queue.

        StatusUpdate and Artifact events are streamed to the client through
        the event queue while also being persisted to the database.
        """
        event_copy = copy.deepcopy(event)
        await self._queue.enqueue_event(event_copy)

    async def _ensure_task_started(self) -> None:
        if self._first_event:
            await self._task_updater.start_work()
            self._first_event = False

    async def _prepare_event(self, event):
        if self._file_transformer:
            event = await self._file_transformer.transform_event(event, wait_upload=False)
        return event

    async def _deduplicate_event(self, event):
        # Skip deduplication for artifact updates - each update should be streamed
        # as a new part of the artifact (e.g., new chunks during streaming)
        if isinstance(event, TaskArtifactUpdateEvent):
            return event

        try:
            task_manager = self._task_manager
        except RuntimeError:
            return event

        if task_manager is None:
            return event

        original_task = await task_manager.get_task()
        if original_task is None:
            return event

        if self._deduplicator is None:
            self._deduplicator = A2ATaskDeduplicator(original_task)

        try:
            return self._deduplicator.deduplicate(event)
        except TypeError:
            return event
