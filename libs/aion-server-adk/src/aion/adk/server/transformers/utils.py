"""Utility functions for GenAI ↔ A2A part conversion."""

from typing import Optional

from a2a import types as a2a_types
from google.genai import types as genai_types
from google.protobuf import json_format, struct_pb2

from aion.adk.authoring.transformers import (
    a2a_part_to_genai_part,  # noqa: F401 — re-exported for callers that import from here
    MIME_TYPE_DATA_PART as _MIME_TYPE_DATA_PART,
    DATA_PART_START_TAG as _DATA_PART_START_TAG,
    DATA_PART_END_TAG as _DATA_PART_END_TAG,
    META_TYPE_KEY as _META_TYPE_KEY,
    META_TYPE_FUNCTION_CALL as _META_TYPE_FUNCTION_CALL,
    META_TYPE_FUNCTION_RESPONSE as _META_TYPE_FUNCTION_RESPONSE,
    META_TYPE_CODE_EXECUTION_RESULT as _META_TYPE_CODE_EXECUTION_RESULT,
    META_TYPE_EXECUTABLE_CODE as _META_TYPE_EXECUTABLE_CODE,
    META_THOUGHT_KEY as _META_THOUGHT_KEY,
    META_VIDEO_METADATA_KEY as _META_VIDEO_METADATA_KEY,
)
from aion.core.logging import get_logger

logger = get_logger()


def _dict_to_value(value: dict) -> struct_pb2.Value:
    """Convert a Python dict to a protobuf Value (struct_value)."""
    _val = struct_pb2.Value()
    json_format.ParseDict(value, _val)
    return _val


def genai_part_to_a2a_part(part: genai_types.Part) -> Optional[a2a_types.Part]:
    """Convert a Google GenAI Part to an A2A Part.

    Equivalent to ADK's convert_genai_part_to_a2a_part but without the
    @a2a_experimental decorator. For file_data, the display_name is mapped
    to Part.filename.
    """
    if part.text:
        kwargs: dict = {"text": part.text}
        if part.thought is not None:
            kwargs["metadata"] = {_META_THOUGHT_KEY: part.thought}
        return a2a_types.Part(**kwargs)

    if part.file_data:
        return a2a_types.Part(
            url=part.file_data.file_uri,
            media_type=part.file_data.mime_type,
            filename=part.file_data.display_name,
        )

    if part.inline_data:
        data = part.inline_data.data
        if (
            part.inline_data.mime_type == _MIME_TYPE_DATA_PART
            and data is not None
            and data.startswith(_DATA_PART_START_TAG)
            and data.endswith(_DATA_PART_END_TAG)
        ):
            json_str = data[len(_DATA_PART_START_TAG):-len(_DATA_PART_END_TAG)].decode()
            return json_format.Parse(json_str, a2a_types.Part())

        kwargs = {
            "raw": data,
            "media_type": part.inline_data.mime_type,
        }
        if part.video_metadata:
            kwargs["metadata"] = {
                _META_VIDEO_METADATA_KEY: part.video_metadata.model_dump(
                    by_alias=True, exclude_none=True
                )
            }
        return a2a_types.Part(**kwargs)

    if part.function_call:
        return a2a_types.Part(
            data=_dict_to_value(
                part.function_call.model_dump(by_alias=True, exclude_none=True)
            ),
            metadata={_META_TYPE_KEY: _META_TYPE_FUNCTION_CALL},
        )

    if part.function_response:
        return a2a_types.Part(
            data=_dict_to_value(
                part.function_response.model_dump(by_alias=True, exclude_none=True)
            ),
            metadata={_META_TYPE_KEY: _META_TYPE_FUNCTION_RESPONSE},
        )

    if part.code_execution_result:
        return a2a_types.Part(
            data=_dict_to_value(
                part.code_execution_result.model_dump(by_alias=True, exclude_none=True)
            ),
            metadata={_META_TYPE_KEY: _META_TYPE_CODE_EXECUTION_RESULT},
        )

    if part.executable_code:
        return a2a_types.Part(
            data=_dict_to_value(
                part.executable_code.model_dump(by_alias=True, exclude_none=True)
            ),
            metadata={_META_TYPE_KEY: _META_TYPE_EXECUTABLE_CODE},
        )

    logger.warning("Cannot convert unsupported GenAI part: %s", part)
    return None



__all__ = ["genai_part_to_a2a_part", "a2a_part_to_genai_part"]
