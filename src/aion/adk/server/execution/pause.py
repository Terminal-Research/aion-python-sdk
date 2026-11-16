"""Where an ADK run pauses for the user, and how the user's reply resumes it.

ADK pauses a run on a long-running tool call: a call whose id is in the
event's ``long_running_tool_ids`` and which no function response has
answered yet. ``LongRunningFunctionTool``, the built-in ``request_input``
tool, ``tool_context.request_confirmation`` (``adk_request_confirmation``)
and ``tool_context.request_credential`` (``adk_request_credential``) all
end that way. The agent returns, and the call waits in the session until a
function response with its id arrives.

On the A2A side the pause looks the way a LangGraph ``interrupt()`` does:
``INPUT_REQUIRED`` with one text part asking the question and the call's id
as ``interruptId``. The user answers with an ordinary message, and the
server turns it into the function response ADK waits for. A data part is
the response as it is; otherwise the response is the JSON object the text
holds, or the text's value as ``{"result": ...}``.

A confirmation (``adk_request_confirmation``) is ADK's ``ToolConfirmation``,
and the server judges no answer on the agent's behalf: a reply that is that
object - a data part, or text holding it as JSON - is the response as it is,
and anything else, a text or a pressed button's data, reaches the agent
unconfirmed, as the confirmation's ``payload`` (``{"text": ...}``,
``{"data": ...}``). A tool that asked with ``tool_context.request_confirmation``
reads it there and decides; a tool ADK confirms for itself
(``require_confirmation=True``) is confirmed by the object, or by the agent's
``before_tool_callback``, which runs before ADK checks the confirmation and
may set it from the payload.

The files the reply carries follow the function response in the same
content, so the agent's model reads them with the answer.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from a2a.types import Message
from aion.adk.server.transformers.a2a_to_adk import file_part
from google.adk.events import Event
from google.adk.tools.tool_confirmation import ToolConfirmation
from google.genai import types
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

__all__ = [
    "CONFIRMATION_CALL",
    "CREDENTIAL_CALL",
    "DEFAULT_QUESTION",
    "INPUT_CALL",
    "PendingCall",
    "paused_on",
    "reply_content",
    "waiting_call",
]

# The names ADK gives the calls its own pausing tools make.
INPUT_CALL = "adk_request_input"
CONFIRMATION_CALL = "adk_request_confirmation"
CREDENTIAL_CALL = "adk_request_credential"

DEFAULT_QUESTION = "Agent requires input"
"""What the user is asked when the call names no question: the text a
LangGraph ``interrupt()`` without a prompt asks with."""


@dataclass(frozen=True)
class PendingCall:
    """A long-running tool call the run paused on.

    call_id       — the call's id, which the function response must carry.
    name          — the called function's name.
    args          — the call's arguments.
    author        — the agent that made the call, which a reply resumes.
    invocation_id — the invocation the call belongs to.
    """

    call_id: str
    name: str
    args: dict[str, Any]
    author: str
    invocation_id: str

    @property
    def question(self) -> str:
        """What the user is asked: the request's message, the confirmation's hint, or a generic question."""
        if self.name == INPUT_CALL:
            message = self.args.get("message")
            if isinstance(message, str) and message:
                return message
        elif self.name == CONFIRMATION_CALL:
            confirmation = self.args.get("toolConfirmation") or {}
            hint = confirmation.get("hint") if isinstance(confirmation, dict) else None
            if isinstance(hint, str) and hint:
                return hint
        return DEFAULT_QUESTION


def _long_running_calls(event: Event) -> list[PendingCall]:
    if event.partial or not event.long_running_tool_ids:
        return []
    return [
        PendingCall(
            call_id=call.id,
            name=call.name or "",
            args=dict(call.args or {}),
            author=event.author or "",
            invocation_id=event.invocation_id or "",
        )
        for call in event.get_function_calls()
        if call.id in event.long_running_tool_ids
    ]


def paused_on(last_events: Sequence[Event]) -> Optional[PendingCall]:
    """The call a finished run paused on, or None when it ran to its end.

    ADK pauses right after the event that makes a long-running call, or
    after the response the tool gave to it at once, so the call is in one of
    the run's last two events - the same window ADK's ``LlmAgent`` checks
    before it stops.

    Args:
        last_events: The run's last non-partial events, oldest first.
    """
    for event in reversed(last_events[-2:]):
        calls = _long_running_calls(event)
        if calls:
            return calls[-1]
    return None


def waiting_call(events: Iterable[Event]) -> Optional[PendingCall]:
    """The long-running call a session still waits on, or None.

    The newest such call wins; a call is answered once a function response
    with its id is in the session after it.
    """
    pending: Optional[PendingCall] = None
    for event in events:
        for response in event.get_function_responses():
            if pending is not None and response.id == pending.call_id:
                pending = None
        calls = _long_running_calls(event)
        if calls:
            pending = calls[-1]
    return pending


def _response(call: PendingCall, message: Optional[Message]) -> dict[str, Any]:
    parts = list(message.parts) if message is not None else []
    data = next((MessageToDict(part.data) for part in parts if part.HasField("data")), None)
    text = "".join(part.text for part in parts if part.HasField("text"))
    if call.name == CONFIRMATION_CALL:
        return _confirmation(data, text)
    if data is not None:
        return data if isinstance(data, dict) else {"result": data}
    parsed = _json(text)
    if parsed is _NOT_JSON:
        return {"result": text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _confirmation(data: Any, text: str) -> dict[str, Any]:
    """The confirmation a reply gives: ADK's object as it is, else the reply itself, unconfirmed."""
    for candidate in (data, _json(text)):
        if _is_tool_confirmation(candidate):
            return candidate
    reply: dict[str, Any] = {}
    if text:
        reply["text"] = text
    if data is not None:
        reply["data"] = data
    return {"payload": reply}


def _is_tool_confirmation(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        ToolConfirmation.model_validate(value)
    except ValidationError:
        return False
    return True


_NOT_JSON = object()
"""What ``_json`` returns for text that holds no JSON; ``null`` is JSON."""


def _json(text: str) -> Any:
    """The JSON value ``text`` holds, or ``_NOT_JSON``."""
    try:
        return json.loads(text)
    except ValueError:
        return _NOT_JSON


def reply_content(call: PendingCall, message: Optional[Message]) -> types.Content:
    """The user's reply to ``call``, as the function response ADK resumes with.

    The reply's files come after the function response, in the order the
    message has them, as files of an ordinary message do
    (``ADKTransformer.transform_context``).

    Args:
        call: The call the session waits on.
        message: The user's A2A message.
    """
    answer = types.Part(
        function_response=types.FunctionResponse(
            id=call.call_id,
            name=call.name,
            response=_response(call, message),
        )
    )
    files = [file_part(part) for part in message.parts if part.raw or part.url] if message is not None else []
    return types.Content(role="user", parts=[answer, *files])
