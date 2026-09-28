"""Invocation-scoped acknowledgment and message provenance, not payload copying."""

from dataclasses import dataclass

from a2a.server.context import ServerCallContext
from a2a.types import Message, Role, Task, TaskStatusUpdateEvent


@dataclass(frozen=True)
class ResponseServiceParameters:
    """Receiver-confirmed response head kept outside protocol event JSON.

    Construct only from the runtime verifier, never from request headers alone.
    """

    activated_extensions: tuple[str, ...] = ()

    def record(self, context: ServerCallContext) -> None:
        """Publish the verified head before a transport commits its headers."""
        context.state['aion.response_service_parameters'] = self

    @classmethod
    def from_context(cls, context: ServerCallContext) -> 'ResponseServiceParameters':
        """Read this invocation's head; reads and unverified requests are empty."""
        return context.state.get('aion.response_service_parameters', cls())

    def annotate(self, event, prior_message_ids: frozenset[str]):
        """Copy and annotate new agent messages before persistence or delivery.

        Args:
            event: Runtime-produced Message, Task, or status update.
            prior_message_ids: Immutable history/status baseline at invocation start.

        Returns:
            A protobuf copy when annotation applies; otherwise the original event.
            Historical and user messages keep their original extension declarations.
        """
        if not self.activated_extensions or not isinstance(
            event, (Message, Task, TaskStatusUpdateEvent)
        ):
            return event
        result = type(event)()
        result.CopyFrom(event)
        messages = []
        if isinstance(result, Message):
            messages.append(result)
        else:
            if isinstance(result, Task):
                messages.extend(result.history)
            if result.status.HasField('message'):
                messages.append(result.status.message)
        for message in messages:
            if message.role != Role.ROLE_AGENT or message.message_id in prior_message_ids:
                continue
            extensions = dict.fromkeys((*message.extensions, *self.activated_extensions))
            del message.extensions[:]
            message.extensions.extend(extensions)
        return result
