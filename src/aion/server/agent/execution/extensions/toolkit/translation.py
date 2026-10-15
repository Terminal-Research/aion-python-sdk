"""A toolkit's protocol values as A2A events, with the invariants the server keeps.

One ``TurnTranslator`` serves one turn of one task. The toolkit yields
``aion.core.toolkit`` values; the translator turns each into the A2A events
the executor's pipeline delivers and stores:

=========================  ====================================================
Protocol value             A2A
=========================  ====================================================
``ToolkitMessageEvent``    ``WORKING`` status update with the message, and the
                           toolkit's object under its URI in the event's
                           metadata
the same, ``ephemeral``    the same update marked ``aion:ephemeral`` and
                           without the object: delivered, never stored
the same, ``partial``      an ``aion:stream-delta`` chunk, closed by the next
                           whole message or by the result
``ToolkitArtifactEvent``   ``TaskArtifactUpdateEvent``; its ``task_metadata``
                           follows as a ``WORKING`` update with no message
``ToolkitStatusEvent``     ``WORKING`` update with no message, carrying only
                           the object
``ToolkitResult``          its artifacts with ``last_chunk``, then the final
                           status in the mapped state, its message closing
                           with the error, if any
=========================  ====================================================

The toolkit's object is the dictionary under its URI in ``task.metadata``. A
``task_metadata`` patch replaces top-level keys of it and ``None`` deletes
one; the translator applies the patch and writes the whole object under the
key, because a2a-sdk merges event metadata into the task with
``Struct.MergeFrom``, which replaces a key's value rather than merging into
it.

The artifacts a toolkit yields are numbered by the server: the first event of
an artifact id in a task stamps ``metadata["version"]`` with the number the
turn's ``version_of`` gives, and every later chunk or replacement of that id
in the task carries the same number.

Every message gets the Event extension's markup: ``type``, ``source`` (the
toolkit's URI), ``id``, plus ``sequence`` - its place among the messages of
the turn - and ``turn`` - the id of the user message that started it; a data
part's schema goes to the part's Event metadata.

What the server enforces whatever the toolkit does:

* every string a toolkit yields is masked against the deployment's secret
  values (``SecretMasker``);
* a text part is cut to ``MAX_MESSAGE_TEXT_LENGTH`` characters in a message
  and to ``MAX_ARTIFACT_TEXT_LENGTH`` in an artifact;
* at most ``MAX_DURABLE_MESSAGES_PER_TURN`` messages of a turn are stored;
  later ones are delivered to the subscribers connected now only;
* a file given as bytes or as a path on this host travels as bytes, which the
  pipeline stores and replaces with a link; a file the toolkit gives as a URL
  is passed on as one;
* the final status closes an open stream delta and every artifact the turn
  left unfinished.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Optional

from a2a.server.events import Event
from a2a.types import (
    Artifact as A2AArtifact,
    Message as A2AMessage,
    Part as A2APart,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf import json_format, struct_pb2
from pydantic_core import to_jsonable_python

from aion.core.a2a.enums import (
    ArtifactId,
    ArtifactName,
    ArtifactStreamingStatus,
    ArtifactStreamingStatusReason,
)
from aion.core.a2a.extensions.event import EventMessageMetadataV1, EventPartMetadataV1
from aion.core.constants.a2a import (
    EVENT_EXTENSION_URI_V1,
    MESSAGING_EXTENSION_URI_V1,
    STREAM_DELTA_PAYLOAD_SCHEMA_V1,
)
from aion.core.exceptions import AionError
from aion.core.toolkit import (
    ARTIFACT_VERSION_KEY,
    Artifact,
    DataPart,
    FilePart,
    Message,
    Part,
    TextPart,
    ToolkitArtifactEvent,
    ToolkitError,
    ToolkitEvent,
    ToolkitMessageEvent,
    ToolkitResult,
    ToolkitState,
    ToolkitStatusEvent,
    codes,
)
from aion.server.a2a.toolkit_errors import ERROR_SCHEMA_NAME, error_parts, error_schema
from aion.server.a2a.utils import mark_status_event_ephemeral

from .secret_values import SecretMasker

__all__ = [
    "CRASH_MESSAGE",
    "DEFAULT_EVENT_TYPE",
    "ERROR_SCHEMA_NAME",
    "MAX_ARTIFACT_TEXT_LENGTH",
    "MAX_DURABLE_MESSAGES_PER_TURN",
    "MAX_MESSAGE_TEXT_LENGTH",
    "TRUNCATION_MARK",
    "ToolkitProtocolError",
    "TurnTranslator",
    "error_schema",
]

logger = logging.getLogger(__name__)

MAX_MESSAGE_TEXT_LENGTH = 16_384
"""Characters a text part of a message keeps; the rest is cut."""

MAX_ARTIFACT_TEXT_LENGTH = 1_048_576
"""Characters a text part of an artifact keeps; the rest is cut."""

MAX_DURABLE_MESSAGES_PER_TURN = 200
"""Messages of one turn the task stores; later ones are delivered live only."""

TRUNCATION_MARK = "\n[truncated]"
"""What ends a text part the server cut."""

DEFAULT_EVENT_TYPE = "to.aion.toolkit.message"
"""The Event type of a message whose toolkit named none."""

CRASH_MESSAGE = (
    "The toolkit stopped on an unexpected error. The server log has the details under task {task_id}."
)
"""What a task says when its turn broke the protocol or crashed; the details go to the log."""

_TASK_STATES = {
    ToolkitState.COMPLETED: TaskState.TASK_STATE_COMPLETED,
    ToolkitState.FAILED: TaskState.TASK_STATE_FAILED,
    ToolkitState.CANCELED: TaskState.TASK_STATE_CANCELED,
    ToolkitState.REJECTED: TaskState.TASK_STATE_REJECTED,
    ToolkitState.INPUT_REQUIRED: TaskState.TASK_STATE_INPUT_REQUIRED,
    ToolkitState.AUTH_REQUIRED: TaskState.TASK_STATE_AUTH_REQUIRED,
}


class ToolkitProtocolError(AionError):
    """A toolkit yielded something the protocol does not allow, or the server could not use it.

    The turn fails with ``internal_error``; this message goes to the log only.
    """


def _struct(value: Mapping[str, Any]) -> struct_pb2.Struct:
    struct = struct_pb2.Struct()
    json_format.ParseDict(value, struct)
    return struct


class TurnTranslator:
    """Translates one turn of one task, keeping the toolkit's object and the turn's counters.

    Args:
        uri: The toolkit's extension URI.
        task_id: The task being served.
        context_id: The task's context.
        turn_id: Id of the user message that started the turn.
        task_metadata: The toolkit's object as the task holds it now.
        masker: The secret values to mask in everything translated.
        version_of: The version number the task writes an artifact id under
            (``TurnArtifacts.number``); asked once per id in a turn. Without
            it the artifacts are not numbered.
    """

    def __init__(
            self,
            *,
            uri: str,
            task_id: str,
            context_id: str,
            turn_id: str,
            task_metadata: Mapping[str, Any],
            masker: SecretMasker,
            version_of: Optional[Callable[[str], Awaitable[int]]] = None,
    ) -> None:
        self._uri = uri
        self._task_id = task_id
        self._context_id = context_id
        self._turn_id = turn_id
        self._object: dict[str, Any] = copy.deepcopy(dict(task_metadata))
        self._object_written = False
        self._masker = masker
        self._version_of = version_of
        self._versions: dict[str, int] = {}
        self._sequence = 0
        self._durable_messages = 0
        self._delta_open = False
        self._open_artifacts: set[str] = set()
        self._limit_reported = False
        self._truncation_reported = False

    @property
    def task_metadata(self) -> dict[str, Any]:
        """The toolkit's object as this turn has left it."""
        return copy.deepcopy(self._object)

    async def event(self, event: ToolkitEvent) -> list[Event]:
        """The A2A events of one event the toolkit yielded.

        Raises:
            ToolkitProtocolError: The event cannot be translated - a file at a
                path the server cannot read, metadata that is not JSON.
        """
        try:
            if isinstance(event, ToolkitMessageEvent):
                return await self._message_event(event)
            if isinstance(event, ToolkitArtifactEvent):
                return await self._artifact_event(event)
            if isinstance(event, ToolkitStatusEvent):
                self._patch(event.task_metadata)
                return [self._details_update()]
        except ToolkitProtocolError:
            raise
        except Exception as error:
            raise ToolkitProtocolError(f"cannot translate {type(event).__name__}: {error}") from error
        raise ToolkitProtocolError(f"{type(event).__name__} is not a toolkit event")

    async def result(self, result: ToolkitResult) -> list[Event]:
        """The A2A events that close the turn: the result's artifacts, then the final status.

        Raises:
            ToolkitProtocolError: The result cannot be translated, or its
                error has an empty code.
        """
        try:
            return await self._result(result)
        except ToolkitProtocolError:
            raise
        except Exception as error:
            raise ToolkitProtocolError(f"cannot translate the result: {error}") from error

    def failure(self) -> list[Event]:
        """The events that close a turn the toolkit broke: ``FAILED`` with ``internal_error``.

        The message is the server's own; why the turn broke is in the log.
        """
        events: list[Event] = self._close_artifacts()
        self._delta_open = False
        text = CRASH_MESSAGE.format(task_id=self._task_id)
        message = self._a2a_message(None, self._error_parts(codes.INTERNAL_ERROR, text, False))
        events.append(self._status(TaskState.TASK_STATE_FAILED, message, changed=False))
        return events

    # -- messages -----------------------------------------------------------

    async def _message_event(self, event: ToolkitMessageEvent) -> list[Event]:
        if event.partial:
            return [self._delta_chunk(event.message)]
        self._delta_open = False
        message = await self._message(event.message)
        if event.ephemeral:
            return [self._ephemeral_update(message)]
        if self._durable_messages >= MAX_DURABLE_MESSAGES_PER_TURN:
            if not self._limit_reported:
                self._limit_reported = True
                logger.warning(
                    "Toolkit %s stored %d messages this turn; later messages of task %s "
                    "reach the subscribers connected now only",
                    self._uri,
                    MAX_DURABLE_MESSAGES_PER_TURN,
                    self._task_id,
                )
            events = [self._ephemeral_update(message)]
            if event.task_metadata is not None:
                self._patch(event.task_metadata)
                events.append(self._details_update())
            return events
        self._durable_messages += 1
        changed = self._patch(event.task_metadata)
        return [self._status(TaskState.TASK_STATE_WORKING, message, changed=changed)]

    async def _message(self, message: Message, extra_parts: tuple[A2APart, ...] = ()) -> A2AMessage:
        parts = [await self._part(part, MAX_MESSAGE_TEXT_LENGTH) for part in message.parts]
        return self._a2a_message(message, [*parts, *extra_parts])

    def _a2a_message(self, message: Optional[Message], parts: list[A2APart]) -> A2AMessage:
        """An agent message with the Event markup; ``message`` gives its type and metadata."""
        self._sequence += 1
        message_id = uuid.uuid4().hex
        markup = EventMessageMetadataV1(
            type=(message.type if message is not None else None) or DEFAULT_EVENT_TYPE,
            source=self._uri,
            id=message_id,
        ).model_dump(by_alias=True)
        markup["sequence"] = self._sequence
        markup["turn"] = self._turn_id
        metadata = self._json(message.metadata) if message is not None else {}
        metadata[EVENT_EXTENSION_URI_V1] = markup
        return A2AMessage(
            message_id=message_id,
            task_id=self._task_id,
            context_id=self._context_id,
            role=Role.ROLE_AGENT,
            parts=parts,
            metadata=_struct(metadata),
            extensions=[EVENT_EXTENSION_URI_V1],
        )

    def _delta_chunk(self, message: Message) -> TaskArtifactUpdateEvent:
        """One piece of text still being written, as the framework adapters stream theirs.

        The first chunk after a whole message opens the ``aion:stream-delta``
        artifact; the next ones append to it. The message's metadata goes to
        the chunk's artifact, under the server's streaming status.
        """
        parts = [
            A2APart(text=self._text(part.text, MAX_MESSAGE_TEXT_LENGTH), metadata=self._part_metadata(part))
            for part in message.parts
            if isinstance(part, TextPart)
        ]
        append = self._delta_open
        self._delta_open = True
        metadata = self._json(message.metadata)
        metadata["status"] = ArtifactStreamingStatus.ACTIVE.value
        metadata["status_reason"] = ArtifactStreamingStatusReason.CHUNK_STREAMING.value
        return TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            metadata=_struct({MESSAGING_EXTENSION_URI_V1: {"schema": STREAM_DELTA_PAYLOAD_SCHEMA_V1}}),
            artifact=A2AArtifact(
                artifact_id=ArtifactId.STREAM_DELTA.value,
                name=ArtifactName.STREAM_DELTA.value,
                parts=parts,
                metadata=_struct(metadata),
            ),
            append=append,
            last_chunk=False,
        )

    def _ephemeral_update(self, message: A2AMessage) -> TaskStatusUpdateEvent:
        event = TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=message),
        )
        mark_status_event_ephemeral(event)
        return event

    # -- artifacts ----------------------------------------------------------

    async def _artifact_event(self, event: ToolkitArtifactEvent) -> list[Event]:
        artifact = await self._artifact(event.artifact)
        if event.last_chunk:
            self._open_artifacts.discard(event.artifact.id)
        else:
            self._open_artifacts.add(event.artifact.id)
        events: list[Event] = [
            TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=artifact,
                append=event.append,
                last_chunk=event.last_chunk,
            )
        ]
        if event.task_metadata is not None:
            self._patch(event.task_metadata)
            events.append(self._details_update())
        return events

    async def _artifact(self, artifact: Artifact) -> A2AArtifact:
        built = A2AArtifact(
            artifact_id=artifact.id,
            parts=[await self._part(part, MAX_ARTIFACT_TEXT_LENGTH) for part in artifact.parts],
        )
        if artifact.name is not None:
            built.name = self._masker.text(artifact.name)
        if artifact.description is not None:
            built.description = self._masker.text(artifact.description)
        if artifact.metadata:
            built.metadata.CopyFrom(_struct(self._json(artifact.metadata)))
        if self._version_of is not None:
            built.metadata[ARTIFACT_VERSION_KEY] = str(await self._version(artifact.id))
        return built

    async def _version(self, artifact_id: str) -> int:
        """The number the task writes ``artifact_id`` under; asked of the store on its first event only."""
        if artifact_id not in self._versions:
            self._versions[artifact_id] = await self._version_of(artifact_id)
        return self._versions[artifact_id]

    def _close_artifacts(self) -> list[Event]:
        """An empty last chunk for every artifact the turn left unfinished; it adds nothing to it."""
        events: list[Event] = [
            TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=A2AArtifact(artifact_id=artifact_id),
                append=True,
                last_chunk=True,
            )
            for artifact_id in sorted(self._open_artifacts)
        ]
        self._open_artifacts.clear()
        return events

    # -- result -------------------------------------------------------------

    async def _result(self, result: ToolkitResult) -> list[Event]:
        if result.error is not None and not result.error.code:
            raise ToolkitProtocolError("the result's error has an empty code")
        self._delta_open = False
        events: list[Event] = []
        for artifact in result.artifacts:
            self._open_artifacts.discard(artifact.id)
            events.append(
                TaskArtifactUpdateEvent(
                    task_id=self._task_id,
                    context_id=self._context_id,
                    artifact=await self._artifact(artifact),
                    append=False,
                    last_chunk=True,
                )
            )
        events.extend(self._close_artifacts())
        changed = self._patch(result.task_metadata)
        message = await self._closing_message(result.message, result.error)
        events.append(self._status(_TASK_STATES[result.state], message, changed=changed))
        return events

    async def _closing_message(
            self, message: Optional[Message], error: Optional[ToolkitError]
    ) -> Optional[A2AMessage]:
        """The final status's message: the result's, then the error for a person and a program."""
        error_parts: tuple[A2APart, ...] = ()
        if error is not None:
            error_parts = tuple(self._error_parts(error.code, error.message, error.retryable))
        if message is not None:
            return await self._message(message, error_parts)
        if error_parts:
            return self._a2a_message(None, list(error_parts))
        return None

    def _error_parts(self, code: str, text: str, retryable: bool) -> list[A2APart]:
        return error_parts(self._uri, code, self._text(text, MAX_MESSAGE_TEXT_LENGTH), retryable)

    # -- the toolkit's object -----------------------------------------------

    def _patch(self, patch: Optional[Mapping[str, Any]]) -> bool:
        """Apply a ``task_metadata`` patch; whether there was one."""
        if patch is None:
            return False
        for key, value in self._json(patch).items():
            if value is None:
                self._object.pop(key, None)
            else:
                self._object[key] = value
        self._object_written = True
        return True

    def _details_update(self) -> TaskStatusUpdateEvent:
        """A ``WORKING`` update with no message, writing the object."""
        return self._status(TaskState.TASK_STATE_WORKING, None, changed=True)

    def _status(self, state: TaskState, message: Optional[A2AMessage], *, changed: bool) -> TaskStatusUpdateEvent:
        """A status update carrying the object whenever there is one to write.

        The object goes on every durable update of a task that has one, so
        each of them leaves it whole in the task; a task whose toolkit never
        wrote an object gets no key.
        """
        event = TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=state, message=message) if message is not None else TaskStatus(state=state),
        )
        if changed or self._object or self._object_written:
            event.metadata[self._uri] = self._object
        return event

    # -- parts and values ---------------------------------------------------

    async def _part(self, part: Part, limit: int) -> A2APart:
        metadata = self._part_metadata(part)
        if isinstance(part, TextPart):
            return A2APart(text=self._text(part.text, limit), metadata=metadata)
        if isinstance(part, DataPart):
            data = struct_pb2.Value()
            json_format.ParseDict(self._json(part.data), data)
            built = A2APart(data=data, media_type="application/json", metadata=metadata)
            if part.data_schema is not None:
                schema = EventPartMetadataV1(schema=self._masker.text(part.data_schema))
                built.metadata[EVENT_EXTENSION_URI_V1] = schema.model_dump(by_alias=True)
            return built
        if isinstance(part, FilePart):
            return await self._file_part(part, metadata)
        raise ToolkitProtocolError(f"{type(part).__name__} is not a part")

    async def _file_part(self, part: FilePart, metadata: Optional[struct_pb2.Struct]) -> A2APart:
        filename = part.filename
        if part.url is not None:
            built = A2APart(url=self._masker.text(part.url), metadata=metadata)
        elif part.raw is not None:
            built = A2APart(raw=part.raw, metadata=metadata)
        else:
            path = Path(part.path)
            try:
                content = await asyncio.to_thread(path.read_bytes)
            except OSError as error:
                raise ToolkitProtocolError(f"cannot read the file part at {part.path}: {error}") from error
            built = A2APart(raw=content, metadata=metadata)
            filename = filename or path.name
        if filename:
            built.filename = self._masker.text(filename)
        if part.media_type:
            built.media_type = part.media_type
        return built

    def _part_metadata(self, part: Part) -> Optional[struct_pb2.Struct]:
        return _struct(self._json(part.metadata)) if part.metadata else None

    def _json(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """A masked, plain-JSON copy of a mapping the toolkit gave."""
        return self._masker.json(to_jsonable_python(dict(value)))

    def _text(self, text: str, limit: int) -> str:
        text = self._masker.text(text)
        if len(text) <= limit:
            return text
        if not self._truncation_reported:
            self._truncation_reported = True
            logger.warning(
                "Toolkit %s gave a text part of %d characters in task %s; it was cut to %d",
                self._uri,
                len(text),
                self._task_id,
                limit,
            )
        return text[: limit - len(TRUNCATION_MARK)] + TRUNCATION_MARK
