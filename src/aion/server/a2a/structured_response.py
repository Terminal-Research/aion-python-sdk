"""The structured response of an agent: the object its schema asks for, as an artifact of the task.

An agent built to answer with an object of a schema - LangChain's
``create_agent(response_format=...)``, an ADK ``LlmAgent`` with an
``output_schema`` - answers with that object besides its text. The client
gets it as an artifact named ``structured_response``: one data part holding
the object as JSON. The text of the answer reaches the client as it does
without a schema.
"""

from __future__ import annotations

from typing import Any

from a2a.types import TaskArtifactUpdateEvent
from google.protobuf import json_format
from pydantic_core import to_jsonable_python

from aion.core.a2a import data_artifact
from aion.core.exceptions import TaskFailedError

__all__ = ["STRUCTURED_RESPONSE_ARTIFACT_NAME", "structured_response_event"]

STRUCTURED_RESPONSE_ARTIFACT_NAME = "structured_response"
"""The name of the artifact that carries an agent's structured response."""


def structured_response_event(value: Any, task_id: str, context_id: str) -> TaskArtifactUpdateEvent:
    """The structured response as an artifact with one data part.

    The value is turned into JSON as Pydantic dumps it: a model, a
    dataclass, a dict and what they hold, an enum, a date. NaN and infinity
    become null, as in Pydantic's own JSON.

    Raises:
        TaskFailedError: The value cannot be expressed as JSON.
    """
    try:
        data = to_jsonable_python(value, inf_nan_mode="null")
        artifact = data_artifact(data, name=STRUCTURED_RESPONSE_ARTIFACT_NAME)
    except (ValueError, TypeError, json_format.Error) as e:
        raise TaskFailedError(
            f"The agent's structured response ({type(value).__name__}) cannot be sent as JSON."
        ) from e
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        artifact=artifact,
        append=False,
        last_chunk=True,
    )
