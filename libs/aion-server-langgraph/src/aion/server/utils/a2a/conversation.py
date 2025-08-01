from typing import List

from a2a.types import Task, TaskState, Artifact, Message

from aion.server.types import Conversation, ConversationTaskStatus, ArtifactName


class ConversationBuilder:
    """
    Builder class for constructing Conversation objects from Task collections.

    Provides methods to extract messages and artifacts from tasks and build
    a complete conversation representation with proper status determination.
    """

    @classmethod
    def build_from_tasks(cls, context_id: str, tasks: List[Task]) -> Conversation:
        """
        Build a Conversation object from a list of tasks.

        Args:
           context_id: The context identifier for the conversation
           tasks: List of Task objects to build the conversation from

        Returns:
           Conversation object with extracted messages, artifacts, and status
        """
        if not tasks:
            return Conversation(
                context_id=context_id,
                history=[],
                artifacts=[],
                status=ConversationTaskStatus(state=TaskState.unknown)
            )

        # Extract messages and artifacts from tasks
        all_messages = cls.extract_messages_from_tasks(tasks, reverse=True)
        all_artifacts = cls.extract_artifacts_from_tasks(tasks)

        # Determine status from task with newest message
        conversation_status = tasks[0].status

        return Conversation(
            context_id=context_id,
            history=all_messages,
            artifacts=all_artifacts,
            status=ConversationTaskStatus(state=conversation_status.state)
        )

    @staticmethod
    def extract_messages_from_tasks(tasks: List[Task], reverse: bool = False) -> List[Message]:
        """
        Extract and filter messages from a collection of tasks.

        Filters messages based on metadata type, only including messages
        with type 'message' or no type specified. Includes task result messages.

        Args:
           tasks: List of Task objects to extract messages from
           reverse: Whether to reverse the order of messages within each task

        Returns:
           List of filtered Message objects
        """
        all_messages = []
        for task in tasks:
            if not task.history:
                continue

            task_messages = []
            for message in task.history:
                task_metadata = getattr(message, 'metadata', None)
                if not task_metadata:
                    task_messages.append(message)
                    continue

                message_type = task_metadata.get('aion:message_type')
                if message_type is None:
                    task_messages.append(message)
                    continue

                if message_type == 'message':
                    task_messages.append(message)
                    continue

            result_message = task.status.message
            if result_message is not None:
                task_messages.append(result_message)

            if task_messages:
                all_messages.extend(reversed(task_messages) if reverse else task_messages)

        return all_messages

    @staticmethod
    def extract_artifacts_from_tasks(tasks: List[Task], reverse: bool = False) -> List[Artifact]:
        """
        Extract and deduplicate artifacts from a collection of tasks.

        Filters out MESSAGE_RESULT artifacts and deduplicates based on artifact ID.
        Artifacts without IDs are always included.

        Args:
           tasks: List of Task objects to extract artifacts from
           reverse: Whether to reverse the final order of artifacts

        Returns:
           List of deduplicated Artifact objects
        """
        all_artifacts = []
        artifact_ids_seen = set()

        for task in tasks:
            if not task.artifacts:
                continue

            for artifact in task.artifacts:
                if artifact.name == ArtifactName.MESSAGE_RESULT.value:
                    continue

                artifact_id = getattr(artifact, 'artifact_id', None) or getattr(artifact, 'id', None)
                if artifact_id and artifact_id not in artifact_ids_seen:
                    all_artifacts.append(artifact)
                    artifact_ids_seen.add(artifact_id)

                elif not artifact_id:
                    all_artifacts.append(artifact)

        return reversed(all_artifacts) if reverse else all_artifacts
