"""A2A ↔ GenAI part conversion utilities for ADK authoring.

Constants and a2a_part_to_genai_part live here so both aion-authoring-adk
(emit_artifact) and aion-server-adk (artifact service, transformer utils)
can share them without a circular dependency.
"""

from a2a import types as a2a_types
from aion.core.logging import get_logger
from google.genai import types as genai_types
from google.protobuf import json_format
from typing import Optional

logger = get_logger()

# Wire-format constants shared with genai_part_to_a2a_part in aion-server-adk.
# Changing these values is a breaking protocol change.
MIME_TYPE_DATA_PART = "text/plain"
DATA_PART_START_TAG = b"<a2a_datapart_json>"
DATA_PART_END_TAG = b"</a2a_datapart_json>"
META_TYPE_KEY = "adk_type"
META_TYPE_FUNCTION_CALL = "function_call"
META_TYPE_FUNCTION_RESPONSE = "function_response"
META_TYPE_CODE_EXECUTION_RESULT = "code_execution_result"
META_TYPE_EXECUTABLE_CODE = "executable_code"
META_THOUGHT_KEY = "adk_thought"
META_VIDEO_METADATA_KEY = "adk_video_metadata"


def a2a_part_to_genai_part(a2a_part: a2a_types.Part) -> Optional[genai_types.Part]:
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


__all__ = [
    "a2a_part_to_genai_part",
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
