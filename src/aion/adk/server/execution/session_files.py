"""Files in an ADK session: kept as links to stored files, not as bytes.

An event can carry a file inline: ``inline_data`` in its content, written by
the agent, a callback or ``thread.post``, and an outbox in its state delta
carries the files of the task or message it answers with. The session
service writes every event whole, so such a file would stay in the database
for as long as the session does.

Every write to the session goes through ``TurnSessionService``, which writes a
copy of the event with each such file stored through the turn's files
(``aion.server.files.turn``): ``inline_data`` becomes ``file_data`` with the
link, keeping the media type and name, and the outbox's parts hold links. A
file the turn also sends to the client is stored once, and the task and the
session link to the same stored file; the SDK's models read it by that link
(``aion.adk.authoring.files``), through a new one once it has expired. The
event the Runner hands on is not changed, so the turn's answer is built from
the event as the agent made it.

A file inside a function response (``FunctionResponse.parts``) is kept as it
is. With no storage backend configured, the session keeps the bytes.
"""

from __future__ import annotations

from typing import Optional

from google.adk.events import Event, EventActions
from google.genai import types

from aion.adk.authoring.transformers import convert_genai_part_to_a2a_part
from aion.core.a2a.models import A2AOutbox
from aion.server.files.turn import TurnFiles, current_turn_files

__all__ = ["OUTBOX_KEY", "kept_in_session"]

OUTBOX_KEY = "a2a_outbox"
"""The state key an agent's outbox is written under."""


async def kept_in_session(event: Event) -> Event:
    """The event as the session keeps it: a copy with its files stored and linked, or the event itself.

    Raises:
        StateFileNotStoredError: A file could not be stored.
    """
    files = current_turn_files()
    if files is None:
        return event
    content = await _content(event.content, files)
    actions = await _actions(event.actions, files)
    if content is event.content and actions is event.actions:
        return event
    return event.model_copy(update={"content": content, "actions": actions})


async def _content(content: Optional[types.Content], files: TurnFiles) -> Optional[types.Content]:
    """The content with its inline files linked, or the content itself when it has none."""
    if content is None or not content.parts:
        return content
    found = []
    for index, part in enumerate(content.parts):
        if part.inline_data is None or not part.inline_data.data:
            continue
        a2a_part = convert_genai_part_to_a2a_part(part)
        # Inline data that holds an A2A data part, not a file, converts to one.
        if a2a_part is None or a2a_part.WhichOneof("content") != "raw":
            continue
        a2a_part.filename = part.inline_data.display_name or ""
        found.append((index, a2a_part))
    if not found:
        return content
    stored = await files.keep_parts([a2a_part for _, a2a_part in found])
    parts = list(content.parts)
    for (index, _), link in zip(found, stored, strict=True):
        inline = parts[index].inline_data
        parts[index] = parts[index].model_copy(
            update={
                "inline_data": None,
                "file_data": types.FileData(
                    file_uri=link.url,
                    mime_type=inline.mime_type,
                    display_name=inline.display_name,
                ),
            }
        )
    return content.model_copy(update={"parts": parts})


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
