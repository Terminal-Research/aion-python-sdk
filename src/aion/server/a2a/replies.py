"""Checking the user's reply against the schema of the question it answers.

A framework task waits for input with a question: its status message, which
carries the question's id under ``interruptId`` and, when the framework
states what the answer must be, the JSON Schema of the reply's value under
``responseSchema`` (``aion.core.a2a.question_metadata``). ``reply_mismatch``
checks a reply's value (``aion.core.a2a.Reply.value``) against that schema
before the task runs, so a reply that does not fit is refused while the
question keeps waiting, the same way for every framework.

Only a reply to the question the status asks is checked: one that names no
question, or names that one. A reply that names another waiting question
has no schema here to be checked against. A schema that is no valid JSON
Schema is not checked against, with a warning: the question still carries
it as the framework stated it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from a2a.types import Message, Task
from google.protobuf import json_format
from jsonschema import exceptions as schema_errors
from jsonschema import validators

from aion.core.a2a import INTERRUPT_ID_KEY, RESPONSE_SCHEMA_KEY

__all__ = ["question_schema", "reply_mismatch"]

logger = logging.getLogger(__name__)


def question_schema(task: Task, message: Message) -> Optional[Any]:
    """The schema of the question ``message`` answers, or None when there is none to check it against.

    Args:
        task: The task that waits for input; its status message is the question.
        message: The reply.
    """
    question = task.status.message
    if not question.metadata:
        return None
    asked = json_format.MessageToDict(question.metadata)
    schema = asked.get(RESPONSE_SCHEMA_KEY)
    if schema is None:
        return None
    named = message.metadata.fields.get(INTERRUPT_ID_KEY) if message.metadata else None
    if named is not None and named.string_value and named.string_value != asked.get(INTERRUPT_ID_KEY):
        return None
    return schema


def reply_mismatch(value: Any, schema: Any) -> Optional[str]:
    """Why ``value`` does not match ``schema``, or None when it does or the schema cannot be checked against.

    Args:
        value: The reply's value.
        schema: The JSON Schema of the answer the question waits for.
    """
    validator_class = validators.validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except schema_errors.SchemaError as error:
        logger.warning("The question's responseSchema is no valid JSON Schema, so replies are not checked against it: %s", error.message)
        return None
    error = schema_errors.best_match(validator_class(schema).iter_errors(value))
    if error is None:
        return None
    where = "".join(f"[{step!r}]" for step in error.absolute_path)
    return f"The reply does not match the question's responseSchema: {error.message}" + (f" (at {where})" if where else "")
