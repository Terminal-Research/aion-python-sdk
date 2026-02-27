"""Utility functions for GenAI ↔ A2A part conversion."""

import base64
from typing import Optional

from a2a import types as a2a_types
from google.genai import types as genai_types

from aion.shared.logging import get_logger

logger = get_logger()

_ADK_METADATA_PREFIX = "adk_"

_MIME_TYPE_DATA_PART = "text/plain"
_DATA_PART_START_TAG = b"<a2a_datapart_json>"
_DATA_PART_END_TAG = b"</a2a_datapart_json>"

_META_TYPE_KEY = f"{_ADK_METADATA_PREFIX}type"
_META_TYPE_FUNCTION_CALL = "function_call"
_META_TYPE_FUNCTION_RESPONSE = "function_response"
_META_TYPE_CODE_EXECUTION_RESULT = "code_execution_result"
_META_TYPE_EXECUTABLE_CODE = "executable_code"
_META_THOUGHT_KEY = f"{_ADK_METADATA_PREFIX}thought"
_META_VIDEO_METADATA_KEY = f"{_ADK_METADATA_PREFIX}video_metadata"


def genai_part_to_a2a_part(part: genai_types.Part) -> Optional[a2a_types.Part]:
    """Convert a Google GenAI Part to an A2A Part.

    Equivalent to ADK's convert_genai_part_to_a2a_part but without the
    @a2a_experimental decorator. For file_data, the display_name is mapped
    to FileWithUri.name.
    """
    if part.text:
        a2a_part = a2a_types.TextPart(text=part.text)
        if part.thought is not None:
            a2a_part.metadata = {_META_THOUGHT_KEY: part.thought}
        return a2a_types.Part(root=a2a_part)

    if part.file_data:
        return a2a_types.Part(
            root=a2a_types.FilePart(
                file=a2a_types.FileWithUri(
                    uri=part.file_data.file_uri,
                    mime_type=part.file_data.mime_type,
                    name=part.file_data.display_name,
                )
            )
        )

    if part.inline_data:
        data = part.inline_data.data
        if (
            part.inline_data.mime_type == _MIME_TYPE_DATA_PART
            and data is not None
            and data.startswith(_DATA_PART_START_TAG)
            and data.endswith(_DATA_PART_END_TAG)
        ):
            return a2a_types.Part(
                root=a2a_types.DataPart.model_validate_json(
                    data[len(_DATA_PART_START_TAG):-len(_DATA_PART_END_TAG)]
                )
            )

        a2a_file = a2a_types.FilePart(
            file=a2a_types.FileWithBytes(
                bytes=base64.b64encode(data).decode("utf-8"),
                mime_type=part.inline_data.mime_type,
            )
        )
        if part.video_metadata:
            a2a_file.metadata = {
                _META_VIDEO_METADATA_KEY: part.video_metadata.model_dump(
                    by_alias=True, exclude_none=True
                )
            }
        return a2a_types.Part(root=a2a_file)

    if part.function_call:
        return a2a_types.Part(
            root=a2a_types.DataPart(
                data=part.function_call.model_dump(by_alias=True, exclude_none=True),
                metadata={_META_TYPE_KEY: _META_TYPE_FUNCTION_CALL},
            )
        )

    if part.function_response:
        return a2a_types.Part(
            root=a2a_types.DataPart(
                data=part.function_response.model_dump(by_alias=True, exclude_none=True),
                metadata={_META_TYPE_KEY: _META_TYPE_FUNCTION_RESPONSE},
            )
        )

    if part.code_execution_result:
        return a2a_types.Part(
            root=a2a_types.DataPart(
                data=part.code_execution_result.model_dump(by_alias=True, exclude_none=True),
                metadata={_META_TYPE_KEY: _META_TYPE_CODE_EXECUTION_RESULT},
            )
        )

    if part.executable_code:
        return a2a_types.Part(
            root=a2a_types.DataPart(
                data=part.executable_code.model_dump(by_alias=True, exclude_none=True),
                metadata={_META_TYPE_KEY: _META_TYPE_EXECUTABLE_CODE},
            )
        )

    logger.warning("Cannot convert unsupported GenAI part: %s", part)
    return None


def a2a_part_to_genai_part(a2a_part: a2a_types.Part) -> Optional[genai_types.Part]:
    """Convert an A2A Part to a Google GenAI Part.

    Equivalent to ADK's convert_a2a_part_to_genai_part but without the
    @a2a_experimental decorator. For FileWithUri, the name is mapped back
    to FileData.display_name.
    """
    part = a2a_part.root

    if isinstance(part, a2a_types.TextPart):
        return genai_types.Part(text=part.text)

    if isinstance(part, a2a_types.FilePart):
        if isinstance(part.file, a2a_types.FileWithUri):
            return genai_types.Part(
                file_data=genai_types.FileData(
                    file_uri=part.file.uri,
                    mime_type=part.file.mime_type,
                    display_name=part.file.name,
                )
            )
        if isinstance(part.file, a2a_types.FileWithBytes):
            return genai_types.Part(
                inline_data=genai_types.Blob(
                    data=base64.b64decode(part.file.bytes),
                    mime_type=part.file.mime_type,
                )
            )
        logger.warning("Cannot convert unsupported file type: %s", type(part.file))
        return None

    if isinstance(part, a2a_types.DataPart):
        meta_type = (part.metadata or {}).get(_META_TYPE_KEY)
        if meta_type == _META_TYPE_FUNCTION_CALL:
            return genai_types.Part(
                function_call=genai_types.FunctionCall.model_validate(
                    part.data, by_alias=True
                )
            )
        if meta_type == _META_TYPE_FUNCTION_RESPONSE:
            return genai_types.Part(
                function_response=genai_types.FunctionResponse.model_validate(
                    part.data, by_alias=True
                )
            )
        if meta_type == _META_TYPE_CODE_EXECUTION_RESULT:
            return genai_types.Part(
                code_execution_result=genai_types.CodeExecutionResult.model_validate(
                    part.data, by_alias=True
                )
            )
        if meta_type == _META_TYPE_EXECUTABLE_CODE:
            return genai_types.Part(
                executable_code=genai_types.ExecutableCode.model_validate(
                    part.data, by_alias=True
                )
            )
        return genai_types.Part(
            inline_data=genai_types.Blob(
                data=_DATA_PART_START_TAG
                + part.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
                + _DATA_PART_END_TAG,
                mime_type=_MIME_TYPE_DATA_PART,
            )
        )

    logger.warning("Cannot convert unsupported A2A part type: %s", type(part))
    return None


__all__ = ["genai_part_to_a2a_part", "a2a_part_to_genai_part"]
