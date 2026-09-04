"""A2A ↔ GenAI part conversion utilities shared across aion-authoring-adk and aion-server-adk."""

import logging
from a2a import types as a2a_types
from google.genai import types as genai_types
from google.protobuf import json_format, struct_pb2
from typing import Optional

logger = logging.getLogger(__name__)

# Wire-format constants shared with genai_part_to_a2a_part in aion-server-adk.
# Changing these values is a breaking protocol change.
MIME_TYPE_DATA_PART = "text/plain"
"""MIME type for generic data parts encoded with JSON wrapper tags."""

DATA_PART_START_TAG = b"<a2a_datapart_json>"
"""Start marker for generic data part JSON encoding in inline_data."""

DATA_PART_END_TAG = b"</a2a_datapart_json>"
"""End marker for generic data part JSON encoding in inline_data."""

META_TYPE_KEY = "adk_type"
"""Metadata key identifying the semantic type of a data part."""

META_TYPE_FUNCTION_CALL = "function_call"
"""Metadata value indicating a function_call Part."""

META_TYPE_FUNCTION_RESPONSE = "function_response"
"""Metadata value indicating a function_response Part."""

META_TYPE_CODE_EXECUTION_RESULT = "code_execution_result"
"""Metadata value indicating a code_execution_result Part."""

META_TYPE_EXECUTABLE_CODE = "executable_code"
"""Metadata value indicating an executable_code Part."""

META_THOUGHT_KEY = "adk_thought"
"""Metadata key carrying model reasoning/thought content."""

META_VIDEO_METADATA_KEY = "adk_video_metadata"
"""Metadata key carrying video file metadata (resolution, duration, etc)."""


def convert_a2a_part_to_genai_part(a2a_part: a2a_types.Part) -> Optional[genai_types.Part]:
    """Convert an A2A Part to a Google GenAI Part.

    Equivalent to ADK's convert_a2a_part_to_genai_part but without the
    @a2a_experimental decorator. For url parts, the filename is mapped back
    to FileData.display_name.
    """
    if a2a_part.text:
        return genai_types.Part(text=a2a_part.text)

    if a2a_part.url:
        return genai_types.Part(
            file_data=genai_types.FileData(
                file_uri=a2a_part.url,
                mime_type=a2a_part.media_type,
                display_name=a2a_part.filename,
            )
        )

    if a2a_part.raw:
        return genai_types.Part(
            inline_data=genai_types.Blob(
                data=a2a_part.raw,
                mime_type=a2a_part.media_type,
            )
        )

    if a2a_part.HasField("data"):
        meta_type = (
            a2a_part.metadata[META_TYPE_KEY]
            if a2a_part.HasField("metadata") and META_TYPE_KEY in a2a_part.metadata
            else None
        )
        data_dict = json_format.MessageToDict(a2a_part.data)

        if meta_type == META_TYPE_FUNCTION_CALL:
            return genai_types.Part(
                function_call=genai_types.FunctionCall.model_validate(
                    data_dict, by_alias=True
                )
            )
        if meta_type == META_TYPE_FUNCTION_RESPONSE:
            return genai_types.Part(
                function_response=genai_types.FunctionResponse.model_validate(
                    data_dict, by_alias=True
                )
            )
        if meta_type == META_TYPE_CODE_EXECUTION_RESULT:
            return genai_types.Part(
                code_execution_result=genai_types.CodeExecutionResult.model_validate(
                    data_dict, by_alias=True
                )
            )
        if meta_type == META_TYPE_EXECUTABLE_CODE:
            return genai_types.Part(
                executable_code=genai_types.ExecutableCode.model_validate(
                    data_dict, by_alias=True
                )
            )

        # Generic data part — encode as inline_data with tags for round-trip
        part_json = json_format.MessageToJson(a2a_part)
        return genai_types.Part(
            inline_data=genai_types.Blob(
                data=DATA_PART_START_TAG + part_json.encode("utf-8") + DATA_PART_END_TAG,
                mime_type=MIME_TYPE_DATA_PART,
            )
        )

    logger.warning("Cannot convert unsupported A2A part type: %s", a2a_part)
    return None


def _dict_to_value(value: dict) -> struct_pb2.Value:
    """Convert a Python dict to a protobuf Struct Value.

    Args:
        value: Dictionary to convert.

    Returns:
        A protobuf Struct Value containing the dictionary data.
    """
    _val = struct_pb2.Value()
    json_format.ParseDict(value, _val)
    return _val


def convert_genai_part_to_a2a_part(part: genai_types.Part) -> Optional[a2a_types.Part]:
    """Convert a Google GenAI Part to an A2A Part."""
    if part.text:
        kwargs: dict = {"text": part.text}
        if part.thought is not None:
            kwargs["metadata"] = {META_THOUGHT_KEY: part.thought}
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
                part.inline_data.mime_type == MIME_TYPE_DATA_PART
                and data is not None
                and data.startswith(DATA_PART_START_TAG)
                and data.endswith(DATA_PART_END_TAG)
        ):
            json_str = data[len(DATA_PART_START_TAG):-len(DATA_PART_END_TAG)].decode()
            return json_format.Parse(json_str, a2a_types.Part())

        kwargs = {
            "raw": data,
            "media_type": part.inline_data.mime_type,
        }
        if part.video_metadata:
            kwargs["metadata"] = {
                META_VIDEO_METADATA_KEY: part.video_metadata.model_dump(
                    by_alias=True, exclude_none=True
                )
            }
        return a2a_types.Part(**kwargs)

    if part.function_call:
        return a2a_types.Part(
            data=_dict_to_value(part.function_call.model_dump(by_alias=True, exclude_none=True)),
            metadata={META_TYPE_KEY: META_TYPE_FUNCTION_CALL},
        )

    if part.function_response:
        return a2a_types.Part(
            data=_dict_to_value(part.function_response.model_dump(by_alias=True, exclude_none=True)),
            metadata={META_TYPE_KEY: META_TYPE_FUNCTION_RESPONSE},
        )

    if part.code_execution_result:
        return a2a_types.Part(
            data=_dict_to_value(part.code_execution_result.model_dump(by_alias=True, exclude_none=True)),
            metadata={META_TYPE_KEY: META_TYPE_CODE_EXECUTION_RESULT},
        )

    if part.executable_code:
        return a2a_types.Part(
            data=_dict_to_value(part.executable_code.model_dump(by_alias=True, exclude_none=True)),
            metadata={META_TYPE_KEY: META_TYPE_EXECUTABLE_CODE},
        )

    logger.warning("Cannot convert unsupported GenAI part: %s", part)
    return None


__all__ = [
    "convert_a2a_part_to_genai_part",
    "convert_genai_part_to_a2a_part",
    "MIME_TYPE_DATA_PART",
    "DATA_PART_START_TAG",
    "DATA_PART_END_TAG",
    "META_TYPE_KEY",
    "META_TYPE_FUNCTION_CALL",
    "META_TYPE_FUNCTION_RESPONSE",
    "META_TYPE_CODE_EXECUTION_RESULT",
    "META_TYPE_EXECUTABLE_CODE",
    "META_THOUGHT_KEY",
    "META_VIDEO_METADATA_KEY",
]
