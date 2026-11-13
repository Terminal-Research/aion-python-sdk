"""Files in an ADK session: kept as links to stored files, not as bytes.

An event can carry a file inline: ``inline_data`` in its content, written by
the agent, a callback, a model or ``thread.post``; a tool's media in its
function response (``FunctionResponse.parts``), where ADK puts an image or a
document a tool returns; and an outbox in its state delta carries the files
of the task or message it answers with. The session service writes every
event whole, so such a file would stay in the database for as long as the
session does.

Every write to the session goes through ``TurnSessionService``, which gives
the session service a copy of the event with each such file stored through
the turn's files (``aion.server.files.turn``) and linked: ``inline_data``
becomes ``file_data`` with the link, keeping the media type and name, and the
outbox's parts hold links. The copy names each stored file by its link in
``custom_metadata`` (``STORED_FILES_KEY``), which the session keeps and a
model is never sent: the SDK's models read a linked file by it, through a new
link once this one has expired (``aion.adk.authoring.files``). A file the
turn also sends to the client is stored once, and the task and the session
link to the same stored file.

The running invocation is not affected: the event the Runner hands on is not
changed, and the invocation's own session goes on with the event's content as
the agent made it (``with_content_of``), so a model later in the same
invocation is sent the bytes whatever model class it is. With no storage
backend configured, the session keeps the bytes.
"""

from __future__ import annotations

from typing import Any, Optional

from a2a.types import Part
from google.adk.events import Event, EventActions
from google.adk.sessions import Session
from google.genai import types

from aion.adk.authoring.files import STORED_FILES_KEY
from aion.adk.authoring.transformers import convert_genai_part_to_a2a_part
from aion.core.a2a.files import StoredFile
from aion.core.a2a.models import A2AOutbox
from aion.server.files.turn import TurnFiles, current_turn_files

__all__ = ["OUTBOX_KEY", "kept_in_session", "with_content_of"]

OUTBOX_KEY = "a2a_outbox"
"""The state key an agent's outbox is written under."""

_Place = tuple[int, Optional[int]]
"""Where a file is in a content: the part's index, and the index in its function response's parts."""


async def kept_in_session(event: Event) -> Event:
    """The event as the session keeps it: a copy with its files stored and linked, or the event itself.

    Raises:
        StateFileNotStoredError: A file could not be stored.
    """
    files = current_turn_files()
    if files is None:
        return event
    content, stored = await _content(event.content, files)
    actions = await _actions(event.actions, files)
    if content is event.content and actions is event.actions:
        return event
    update: dict[str, Any] = {"content": content, "actions": actions}
    if stored:
        update["custom_metadata"] = {**(event.custom_metadata or {}), STORED_FILES_KEY: stored}
    return event.model_copy(update=update)


def with_content_of(session: Session, event: Event) -> None:
    """Give the invocation's session the content ``event`` had before its files were linked.

    The session service has just appended the linked copy to ``session`` - the
    session object the running invocation reads - and kept it in its store.
    The entry in ``session`` is replaced by one with the event's own content,
    so the invocation goes on with the bytes; what is stored keeps the links.
    """
    if session.events and session.events[-1].id == event.id:
        session.events[-1] = session.events[-1].model_copy(update={"content": event.content})


async def _content(
    content: Optional[types.Content], files: TurnFiles
) -> tuple[Optional[types.Content], dict[str, dict]]:
    """The content with its inline files linked, and the stored files by their links.

    The content itself, and no files, when it carries none.
    """
    if content is None or not content.parts:
        return content, {}
    found: list[tuple[_Place, Part]] = []
    for index, part in enumerate(content.parts):
        file = _inline_file(part)
        if file is not None:
            found.append(((index, None), file))
        response = part.function_response
        for media_index, media in enumerate((response.parts or []) if response is not None else []):
            blob = media.inline_data
            if blob is not None and blob.data:
                found.append(
                    ((index, media_index), Part(raw=blob.data, media_type=blob.mime_type or "", filename=blob.display_name or ""))
                )
    if not found:
        return content, {}

    links = await files.keep_parts([file for _, file in found])
    parts = list(content.parts)
    stored: dict[str, dict] = {}
    for ((index, media_index), _), link in zip(found, links, strict=True):
        if media_index is None:
            parts[index] = _linked(parts[index], link)
        else:
            parts[index] = _with_linked_media(parts[index], media_index, link)
        reference = StoredFile.of(link)
        if reference is not None:
            stored[link.url] = reference.to_metadata()
    return content.model_copy(update={"parts": parts}), stored


def _inline_file(part: types.Part) -> Optional[Part]:
    """The A2A part of a file a part carries inline, or None.

    Inline data that holds an A2A data part, not a file, is not one.
    """
    if part.inline_data is None or not part.inline_data.data:
        return None
    a2a_part = convert_genai_part_to_a2a_part(part)
    if a2a_part is None or a2a_part.WhichOneof("content") != "raw":
        return None
    a2a_part.filename = part.inline_data.display_name or ""
    return a2a_part


def _linked(part: types.Part, link: Part) -> types.Part:
    """The part with its inline file replaced by ``file_data`` holding the link."""
    inline = part.inline_data
    return part.model_copy(
        update={
            "inline_data": None,
            "file_data": types.FileData(
                file_uri=link.url, mime_type=inline.mime_type, display_name=inline.display_name
            ),
        }
    )


def _with_linked_media(part: types.Part, media_index: int, link: Part) -> types.Part:
    """The function response part with one of its media replaced by its link."""
    response = part.function_response
    media = list(response.parts)
    inline = media[media_index].inline_data
    media[media_index] = media[media_index].model_copy(
        update={
            "inline_data": None,
            "file_data": types.FunctionResponseFileData(
                file_uri=link.url, mime_type=inline.mime_type, display_name=inline.display_name
            ),
        }
    )
    return part.model_copy(update={"function_response": response.model_copy(update={"parts": media})})


async def _actions(actions: Optional[EventActions], files: TurnFiles) -> Optional[EventActions]:
    """The actions with the files of the outbox they write linked, or the actions themselves."""
    if actions is None or not isinstance(actions.state_delta.get(OUTBOX_KEY), dict):
        return actions
    outbox = A2AOutbox.model_validate(actions.state_delta[OUTBOX_KEY])
    kept = await files.keep_outbox(outbox)
    if kept is outbox:
        return actions
    return actions.model_copy(
        update={"state_delta": {**actions.state_delta, OUTBOX_KEY: kept.model_dump()}}
    )
