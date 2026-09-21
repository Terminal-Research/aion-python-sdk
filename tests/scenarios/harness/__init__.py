"""Everything the scenarios need to run an agent and read what it answered."""

from .callback import CallbackServer
from .client import FileAttachment, ScenarioClient
from .distribution import DISTRIBUTION_EXTENSION_URI, ORGANIZATION_ID, distribution_metadata
from .recorder import Ev, final_task, record_stream, replies, reply_texts
from .serve import ServeProcess, ServeVariant
from .shape import (
    ANY,
    SKIP,
    STREAM_DELTA_ARTIFACT_ID,
    artifact,
    assert_shape,
    chunks,
    ephemeral,
    is_chunk,
    message,
    status,
    task,
)

__all__ = [
    "ANY",
    "CallbackServer",
    "DISTRIBUTION_EXTENSION_URI",
    "Ev",
    "FileAttachment",
    "ORGANIZATION_ID",
    "SKIP",
    "STREAM_DELTA_ARTIFACT_ID",
    "ScenarioClient",
    "ServeProcess",
    "ServeVariant",
    "artifact",
    "assert_shape",
    "chunks",
    "distribution_metadata",
    "ephemeral",
    "final_task",
    "is_chunk",
    "message",
    "record_stream",
    "replies",
    "reply_texts",
    "status",
    "task",
]
