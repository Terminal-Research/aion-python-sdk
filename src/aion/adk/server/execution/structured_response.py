"""The object an ``LlmAgent`` with an ``output_schema`` answers with.

ADK gives the model an ``LlmAgent``'s ``output_schema`` as the schema of its
response, and the agent's final response is the model's JSON text of an
object of that schema - or, when the model cannot answer with a schema and
call tools at once, the JSON of the object the model gave ADK's
``set_model_response`` tool. ADK reads the object back only for an agent with
an ``output_key``, into the session state under that key.

``structured_response`` reads the object of a final response the way ADK
does, for any agent with a schema: the value ADK already put under the
agent's ``output_key``, else the response's text read by the schema - a
Pydantic model dumped without its unset fields, another type validated by
Pydantic, any other schema parsed as JSON. The server sends the turn's last
such object to the client as its ``structured_response`` artifact
(``aion.server.a2a.structured_response``). A response whose text does not
match its schema is left as text, with a warning: ADK lets an agent without
an ``output_key`` answer so.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai import types
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

__all__ = ["NO_STRUCTURED_RESPONSE", "structured_response"]

logger = logging.getLogger(__name__)

NO_STRUCTURED_RESPONSE: Any = object()
"""What ``structured_response`` returns for an event that answers with no object."""

_FENCED = re.compile(r"\A\s*```(?:json)?\s*\n(?P<body>.*?)\n?\s*```\s*\Z", re.DOTALL)


def structured_response(event: Event, root: Optional[BaseAgent]) -> Any:
    """The object a final response answers with by its agent's ``output_schema``, or ``NO_STRUCTURED_RESPONSE``.

    Args:
        event: An event of the run.
        root: The invocation's root agent, in whose tree the event's author
            is found.
    """
    if event.partial or not event.is_final_response() or root is None or not event.author:
        return NO_STRUCTURED_RESPONSE
    agent = root.find_agent(event.author)
    schema = getattr(agent, "output_schema", None)
    if schema is None:
        return NO_STRUCTURED_RESPONSE
    output_key = getattr(agent, "output_key", None)
    if output_key and event.actions is not None and output_key in event.actions.state_delta:
        return event.actions.state_delta[output_key]
    text = _text(event)
    if not text.strip():
        return NO_STRUCTURED_RESPONSE
    try:
        return _read(text, schema)
    except (ValidationError, ValueError) as error:
        logger.warning(
            "The final response of agent %s does not match its output_schema; no structured response is sent: %s",
            event.author,
            error,
        )
        return NO_STRUCTURED_RESPONSE


def _text(event: Event) -> str:
    """The text of the event's content, its thoughts left out."""
    parts = event.content.parts if event.content is not None and event.content.parts else []
    return "".join(part.text for part in parts if part.text and not part.thought)


def _read(text: str, schema: Any) -> Any:
    """The object the text gives by the schema, as JSON-ready data."""
    fenced = _FENCED.match(text)
    if fenced is not None:
        text = fenced["body"]
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate_json(text).model_dump(mode="json", exclude_none=True)
    if isinstance(schema, (dict, types.Schema)):
        return json.loads(text)
    return to_jsonable_python(TypeAdapter(schema).validate_json(text), exclude_none=True)
