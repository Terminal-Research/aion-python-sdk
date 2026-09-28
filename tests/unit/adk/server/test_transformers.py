"""Both ADK transformers, on the ``types.Part`` values ADK really produces.

``A2ATransformer`` turns what a model said into what the client sees, and
``ADKTransformer`` turns what the client sent into what the model reads.
The inputs here are the genai part kinds a model and a user actually send -
thoughts, function calls and responses, executable code and its result,
inline and referenced files - built as ADK builds them, not as mocks.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from a2a.types import Message, Part, Role
from google.genai import types
from google.protobuf import struct_pb2

from aion.adk.server.transformers.a2a_to_adk import ADKTransformer
from aion.adk.server.transformers.adk_to_a2a import A2ATransformer

PNG = b"\x89PNG\r\n\x1a\n"


def _model(*parts: types.Part) -> types.Content:
    return types.Content(role="model", parts=list(parts))


def _kinds(parts: list[Part]) -> list[str]:
    return [part.WhichOneof("content") for part in parts]


class TestWhatTheClientSees:
    """``A2ATransformer.transform_content``: the path every agent message takes."""

    def test_consecutive_text_is_one_part(self) -> None:
        content = _model(types.Part(text="It "), types.Part(text="is "), types.Part(text="sunny."))

        assert [part.text for part in A2ATransformer.transform_content(content)] == ["It is sunny."]

    @pytest.mark.parametrize(
        "hidden",
        [
            types.Part(text="let me think", thought=True),
            types.Part(function_call=types.FunctionCall(name="get_weather", args={"city": "Kyiv"})),
            types.Part(
                function_response=types.FunctionResponse(name="get_weather", response={"result": "sunny"})
            ),
            types.Part(executable_code=types.ExecutableCode(code="print(1)", language="PYTHON")),
            types.Part(
                code_execution_result=types.CodeExecutionResult(outcome="OUTCOME_OK", output="1")
            ),
        ],
        ids=["thought", "function_call", "function_response", "executable_code", "code_result"],
    )
    def test_what_is_not_the_agent_speaking_is_left_out(self, hidden: types.Part) -> None:
        """And it does not split the text around it either."""
        content = _model(types.Part(text="Let me "), hidden, types.Part(text="check."))

        assert [part.text for part in A2ATransformer.transform_content(content)] == ["Let me check."]

    def test_a_content_of_only_hidden_parts_is_nothing(self) -> None:
        content = _model(
            types.Part(text="thinking", thought=True),
            types.Part(function_call=types.FunctionCall(name="f", args={})),
        )

        assert A2ATransformer.transform_content(content) == []

    def test_an_inline_file_keeps_its_place_between_the_texts(self) -> None:
        content = _model(
            types.Part(text="Here: "),
            types.Part(inline_data=types.Blob(mime_type="image/png", data=PNG)),
            types.Part(text="done."),
        )

        parts = A2ATransformer.transform_content(content)

        assert _kinds(parts) == ["text", "raw", "text"]
        assert parts[1].raw == PNG
        assert parts[1].media_type == "image/png"

    def test_a_referenced_file_becomes_a_url(self) -> None:
        content = _model(
            types.Part(file_data=types.FileData(file_uri="https://files/report.pdf", mime_type="application/pdf"))
        )

        (part,) = A2ATransformer.transform_content(content)

        assert part.url == "https://files/report.pdf"
        assert part.media_type == "application/pdf"


class TestOnePartAtATime:
    """``A2ATransformer.transform_part``: what an ADK artifact is emitted as."""

    @pytest.mark.parametrize(
        "hidden",
        [
            types.Part(text="thinking", thought=True),
            types.Part(function_call=types.FunctionCall(name="f", args={})),
            types.Part(function_response=types.FunctionResponse(name="f", response={})),
        ],
        ids=["thought", "function_call", "function_response"],
    )
    def test_calls_and_thoughts_are_none(self, hidden: types.Part) -> None:
        assert A2ATransformer.transform_part(hidden) is None

    @pytest.mark.parametrize(
        "code_part",
        [
            types.Part(executable_code=types.ExecutableCode(code="print(1)", language="PYTHON")),
            types.Part(code_execution_result=types.CodeExecutionResult(outcome="OUTCOME_OK", output="1")),
        ],
        ids=["executable_code", "code_result"],
    )
    def test_code_the_agent_saves_as_an_artifact_is_data(self, code_part: types.Part) -> None:
        """The other road for executed code: an artifact the agent saved on purpose.

        ``transform_content`` leaves these parts out of a reply; an artifact
        is sent because the agent chose to, so its code parts arrive as data.
        The two roads differ by design.
        """
        part = A2ATransformer.transform_part(code_part)

        assert part is not None
        assert part.WhichOneof("content") == "data"

    def test_text_and_files_convert(self) -> None:
        assert A2ATransformer.transform_part(types.Part(text="hi")).text == "hi"
        blob = A2ATransformer.transform_part(types.Part(inline_data=types.Blob(mime_type="image/png", data=PNG)))
        assert blob.raw == PNG


def _context(*parts: Part) -> Mock:
    context = Mock()
    context.message = Message(role=Role.ROLE_USER, parts=list(parts)) if parts else None
    context.get_user_input.return_value = ""
    return context


class TestWhatTheModelReads:
    """``ADKTransformer.transform_context``: every A2A part kind a client sends."""

    def test_text_is_text(self) -> None:
        content = ADKTransformer.transform_context(_context(Part(text="hello")))

        assert content.role == "user"
        assert [part.text for part in content.parts] == ["hello"]

    def test_inline_bytes_become_inline_data_with_their_media_type(self) -> None:
        content = ADKTransformer.transform_context(_context(Part(raw=PNG, media_type="image/png")))

        (part,) = content.parts
        assert part.inline_data.data == PNG
        assert part.inline_data.mime_type == "image/png"

    def test_inline_bytes_without_a_media_type_are_octet_stream(self) -> None:
        (part,) = ADKTransformer.transform_context(_context(Part(raw=b"abc"))).parts

        assert part.inline_data.mime_type == "application/octet-stream"

    @pytest.mark.parametrize(
        ("url_part", "mime_type"),
        [
            (Part(url="https://files/a", media_type="application/pdf"), "application/pdf"),
            (Part(url="https://files/a", filename="notes.txt"), "text/plain"),
            (Part(url="https://files/a"), "application/octet-stream"),
        ],
        ids=["media_type", "from_filename", "fallback"],
    )
    def test_a_url_becomes_file_data(self, url_part: Part, mime_type: str) -> None:
        (part,) = ADKTransformer.transform_context(_context(url_part)).parts

        assert part.file_data.file_uri == "https://files/a"
        assert part.file_data.mime_type == mime_type

    def test_a_data_part_is_its_json_text(self) -> None:
        """Contract: the model reads structured input as JSON text."""
        value = struct_pb2.Value()
        value.struct_value.update({"order": 42, "items": ["tea"]})

        (part,) = ADKTransformer.transform_context(_context(Part(data=value))).parts

        assert json.loads(part.text) == {"order": 42, "items": ["tea"]}

    def test_the_parts_keep_their_order(self) -> None:
        value = struct_pb2.Value()
        value.struct_value.update({"k": 1})

        content = ADKTransformer.transform_context(
            _context(Part(text="look"), Part(raw=PNG, media_type="image/png"), Part(data=value))
        )

        assert [
            "text" if part.text is not None else "inline_data" if part.inline_data else "other"
            for part in content.parts
        ] == ["text", "inline_data", "text"]

    def test_with_no_message_the_user_input_is_used(self) -> None:
        context = _context()
        context.get_user_input.return_value = "from the request"

        content = ADKTransformer.transform_context(context)

        assert [part.text for part in content.parts] == ["from the request"]
