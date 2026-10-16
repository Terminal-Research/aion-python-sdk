"""The values a toolkit hands to the Aion server during a turn.

A turn is a stream of events followed by exactly one :class:`ToolkitResult`.
The server translates every value into A2A on the toolkit's behalf and keeps
the task's invariants (one terminal status, size limits, secret masking,
event numbering, file storage); a toolkit never builds A2A objects itself.
These models do not import a2a: the protocol is the toolkit's contract with
the server, not with the wire.

Three kinds of event, one result:

* :class:`ToolkitMessageEvent` — a message for the task's feed. Durable by
  default (it reaches the task's history); ``ephemeral`` delivers it only to
  the subscribers connected right now, ``partial`` streams a piece of text
  that is still being written.
* :class:`ToolkitArtifactEvent` — an artifact of the task, whole or in chunks.
* :class:`ToolkitStatusEvent` — new details of the task without a message in
  the feed (live usage, progress of a long step).
* :class:`ToolkitResult` — how the turn ended.

``task_metadata`` on an event or the result patches the toolkit's own object
in ``task.metadata[<toolkit uri>]``: top-level keys are replaced and ``None``
deletes a key. ``tasks/get``, a late subscriber and the next turn of the task
all see the object as the last durable write left it.

Metadata on parts, messages and artifacts belongs to the toolkit; keys
starting with ``aion:`` and Aion extension URIs are reserved for the server.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "ARTIFACT_VERSION_KEY",
    "Artifact",
    "DataPart",
    "FilePart",
    "Message",
    "Metadata",
    "Part",
    "TextPart",
    "ToolkitArtifactEvent",
    "ToolkitError",
    "ToolkitEvent",
    "ToolkitMessageEvent",
    "ToolkitOutput",
    "ToolkitResult",
    "ToolkitState",
    "ToolkitStatusEvent",
]

_RESERVED_KEY = re.compile(r"^(aion:|https://docs\.aion\.to/a2a/extensions/)")
_EVENT_TYPE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")

ARTIFACT_VERSION_KEY = "version"
"""Artifact metadata key the server sets: the artifact's version in the context."""


def _check_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Reject metadata keys the server reserves for itself."""
    reserved = sorted(key for key in value if _RESERVED_KEY.match(key))
    if reserved:
        raise ValueError(
            f"metadata keys {reserved} are reserved for the server "
            "('aion:' keys and Aion extension URIs)"
        )
    return value


Metadata = Annotated[dict[str, Any], AfterValidator(_check_metadata)]
"""Free-form JSON metadata of a part, message or artifact; server keys rejected."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TextPart(_Model):
    """Text for a person to read."""

    kind: Literal["text"] = "text"
    text: str
    metadata: Metadata = Field(default_factory=dict)


class DataPart(_Model):
    """Structured data for a program to read.

    ``data_schema`` (``schema`` on the wire) names the shape of ``data`` as
    ``{toolkit extension uri}#{PayloadName}``, so a client parses it without
    guessing from its fields. A message carrying a part with a schema must
    name its event ``type``.
    """

    kind: Literal["data"] = "data"
    data: dict[str, Any]
    data_schema: Optional[str] = Field(default=None, alias="schema")
    metadata: Metadata = Field(default_factory=dict)


class FilePart(_Model):
    """A file, given by exactly one of ``raw`` or ``url``, as an A2A file part.

    ``raw`` is the file's bytes, which the server stores and delivers as a
    link; ``url`` is a file that is already hosted. A file the server cannot
    store fails the task.
    """

    kind: Literal["file"] = "file"
    raw: Optional[bytes] = None
    url: Optional[str] = None
    filename: Optional[str] = None
    media_type: Optional[str] = None
    metadata: Metadata = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_source(self) -> "FilePart":
        sources = [name for name in ("raw", "url") if getattr(self, name) is not None]
        if len(sources) != 1:
            raise ValueError(f"a file part has exactly one of raw, url; got {sources or 'none'}")
        return self


Part = Annotated[Union[TextPart, DataPart, FilePart], Field(discriminator="kind")]


class Message(_Model):
    """A message of the task's feed.

    ``type`` is the event's reverse-DNS name (``stage.changed``,
    ``plan.updated``): a program picks its handler by it instead of reading
    the text. The server adds the event's source and id.
    """

    parts: list[Part] = Field(min_length=1)
    type: Optional[str] = None
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _check_type(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _EVENT_TYPE.match(value):
            raise ValueError(f"event type {value!r} is not a reverse-DNS name such as 'stage.changed'")
        return value

    @model_validator(mode="after")
    def _typed_when_schematized(self) -> "Message":
        if self.type is None and any(
            isinstance(part, DataPart) and part.data_schema is not None for part in self.parts
        ):
            raise ValueError("a message carrying a data part with a schema must name its event type")
        return self


class Artifact(_Model):
    """An artifact of the task.

    ``id`` is the artifact's identity within the task: the same ``id`` is the
    same artifact, replaced or appended to. The same ``id`` in several tasks
    of a context is one artifact in several versions; the server numbers them
    in ``metadata["version"]``, which a toolkit never sets.
    """

    id: str = Field(min_length=1)
    name: Optional[str] = None
    description: Optional[str] = None
    parts: list[Part] = Field(min_length=1)
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if value.startswith("aion:"):
            raise ValueError("artifact ids starting with 'aion:' are reserved for the server")
        return value

    @field_validator("metadata")
    @classmethod
    def _no_version(cls, value: dict[str, Any]) -> dict[str, Any]:
        if ARTIFACT_VERSION_KEY in value:
            raise ValueError("an artifact's 'version' is set by the server")
        return value


class ToolkitMessageEvent(_Model):
    """A message for the task's feed.

    ``ephemeral`` delivers it only to the subscribers connected now: the task
    does not change. ``partial`` is a piece of text still being written,
    closed by the next whole message or the result. Neither may carry
    ``task_metadata``: a detail written to the task must be durable.
    """

    kind: Literal["message"] = "message"
    message: Message
    ephemeral: bool = False
    partial: bool = False
    task_metadata: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _check_flags(self) -> "ToolkitMessageEvent":
        if self.partial and not all(isinstance(part, TextPart) for part in self.message.parts):
            raise ValueError("a partial message carries text parts only")
        if (self.ephemeral or self.partial) and self.task_metadata is not None:
            raise ValueError("an ephemeral or partial message cannot carry task_metadata")
        return self


class ToolkitArtifactEvent(_Model):
    """An artifact, whole or one chunk of it (``append``, ``last_chunk``)."""

    kind: Literal["artifact"] = "artifact"
    artifact: Artifact
    append: bool = False
    last_chunk: bool = False
    task_metadata: Optional[dict[str, Any]] = None


class ToolkitStatusEvent(_Model):
    """New details of the task with no message in its feed."""

    kind: Literal["status"] = "status"
    task_metadata: dict[str, Any] = Field(min_length=1)


class ToolkitState(str, Enum):
    """How a turn ended.

    ``input-required`` and ``auth-required`` close the turn until the user
    answers; the answer starts the next turn of the same task.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    INPUT_REQUIRED = "input-required"
    AUTH_REQUIRED = "auth-required"


class ToolkitError(_Model):
    """Why a turn failed.

    ``code`` is for a client to branch on, ``message`` for a person to read,
    ``retryable`` says whether repeating the same request can succeed. The
    diagnostic detail stays in the toolkit's log.
    """

    code: str = Field(min_length=1)
    message: str
    retryable: bool = False


class ToolkitResult(_Model):
    """The end of a turn: its state, closing message, artifacts and details.

    ``message`` becomes the text of the task's final status. ``error`` is set
    only for ``failed`` and ``rejected``.
    """

    kind: Literal["result"] = "result"
    state: ToolkitState
    message: Optional[Message] = None
    artifacts: list[Artifact] = Field(default_factory=list)
    task_metadata: Optional[dict[str, Any]] = None
    error: Optional[ToolkitError] = None

    @model_validator(mode="after")
    def _error_only_on_failure(self) -> "ToolkitResult":
        if self.error is not None and self.state not in (ToolkitState.FAILED, ToolkitState.REJECTED):
            raise ValueError("a result carries an error only when it failed or was rejected")
        return self


ToolkitEvent = Union[ToolkitMessageEvent, ToolkitArtifactEvent, ToolkitStatusEvent]
"""What a toolkit yields before its result."""

ToolkitOutput = Annotated[
    Union[ToolkitMessageEvent, ToolkitArtifactEvent, ToolkitStatusEvent, ToolkitResult],
    Field(discriminator="kind"),
]
"""Anything a toolkit yields during a turn."""
