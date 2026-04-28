from typing import Protocol

from a2a.server.events import Event
from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent


class AionTaskManagerProtocol(Protocol):
    """Protocol for Aion task managers.

    Defines the interface that task managers should implement for processing
    task-related events and managing task lifecycle.
    """

    async def get_task(self) -> Task | None:
        """Retrieve the current task object, either from memory or the store.

        Returns:
            The Task object if found, otherwise None.
        """
        ...

    async def save_task_event(
        self, event: Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ) -> Task | None:
        """Process a task-related event and save the updated task state.

        Args:
            event: The task-related event (Task, TaskStatusUpdateEvent, or TaskArtifactUpdateEvent).

        Returns:
            The updated Task object after processing the event.
        """
        ...

    async def ensure_task(
        self, event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ) -> Task:
        """Ensure a Task object exists in memory, loading from store or creating if needed.

        Args:
            event: The task-related event triggering the need for a Task object.

        Returns:
            An existing or newly created Task object.
        """
        ...

    async def process(self, event: Event) -> Event:
        """Process an event, update the task state if applicable, and return the event.

        Args:
            event: The event object to process.

        Returns:
            The processed event object.
        """
        ...

    def update_with_message(self, message: Message, task: Task) -> Task:
        """Update a task object in memory by adding a new message to its history.

        Args:
            message: The new Message to add to the history.
            task: The Task object to update.

        Returns:
            The updated Task object (updated in-place).
        """
        ...

    async def auto_discover_and_assign_task(self, interrupted: bool = False) -> Task | None:
        """Automatically discover and assign a task from the current context.

        Retrieves the most recent task associated with the current context
        and assigns it to the task manager. Can optionally filter to only
        assign interrupted tasks.

        Args:
            interrupted: If True, only assign interrupted tasks.

        Returns:
            The assigned task, or None if no task was found or assigned.
        """
        ...
