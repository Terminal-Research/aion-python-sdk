"""The checks every protocol value makes before the server sees it."""

import pytest
from pydantic import TypeAdapter, ValidationError

from aion.core.toolkit import (
    Artifact,
    DataPart,
    FilePart,
    Message,
    TextPart,
    ToolkitArtifactEvent,
    ToolkitError,
    ToolkitMessageEvent,
    ToolkitOutput,
    ToolkitResult,
    ToolkitState,
    ToolkitStatusEvent,
)

OUTPUT = TypeAdapter(ToolkitOutput)


def _text(text: str = "hello") -> Message:
    return Message(parts=[TextPart(text=text)])


class TestParts:
    def test_a_file_has_exactly_one_source(self):
        FilePart(raw=b"x")
        FilePart(path="/tmp/x")
        FilePart(url="https://example.com/x")
        with pytest.raises(ValidationError):
            FilePart()
        with pytest.raises(ValidationError):
            FilePart(raw=b"x", url="https://example.com/x")

    @pytest.mark.parametrize(
        "key",
        ["aion:settledReason", "https://docs.aion.to/a2a/extensions/aion/event/1.0.0"],
    )
    def test_server_keys_are_refused_in_metadata(self, key):
        with pytest.raises(ValidationError, match="reserved for the server"):
            TextPart(text="x", metadata={key: 1})
        with pytest.raises(ValidationError, match="reserved for the server"):
            Message(parts=[TextPart(text="x")], metadata={key: 1})

    def test_a_data_part_reads_its_schema_by_wire_name(self):
        part = DataPart(data={"a": 1}, schema="urn:x#Payload")
        assert part.data_schema == "urn:x#Payload"
        assert part.model_dump(by_alias=True)["schema"] == "urn:x#Payload"


class TestMessage:
    def test_needs_a_part(self):
        with pytest.raises(ValidationError):
            Message(parts=[])

    def test_a_schematized_data_part_needs_an_event_type(self):
        with pytest.raises(ValidationError, match="event type"):
            Message(parts=[DataPart(data={}, schema="urn:x#P")])
        Message(parts=[DataPart(data={}, schema="urn:x#P")], type="plan.updated")
        Message(parts=[DataPart(data={})])

    @pytest.mark.parametrize("value", ["stage", "Stage.Changed", "stage..changed", "stage changed"])
    def test_an_event_type_is_reverse_dns(self, value):
        with pytest.raises(ValidationError):
            Message(parts=[TextPart(text="x")], type=value)


class TestArtifact:
    def test_server_ids_are_refused(self):
        with pytest.raises(ValidationError, match="reserved for the server"):
            Artifact(id="aion:result", parts=[TextPart(text="x")])

    def test_the_version_is_the_servers(self):
        with pytest.raises(ValidationError, match="set by the server"):
            Artifact(id="spec", parts=[TextPart(text="x")], metadata={"version": 2})


class TestEvents:
    def test_a_partial_message_is_text_only(self):
        ToolkitMessageEvent(message=_text(), partial=True)
        with pytest.raises(ValidationError, match="text parts only"):
            ToolkitMessageEvent(message=Message(parts=[DataPart(data={})]), partial=True)

    @pytest.mark.parametrize("flag", ["ephemeral", "partial"])
    def test_task_metadata_needs_a_durable_message(self, flag):
        with pytest.raises(ValidationError, match="task_metadata"):
            ToolkitMessageEvent(message=_text(), task_metadata={"stage": "x"}, **{flag: True})
        ToolkitMessageEvent(message=_text(), task_metadata={"stage": "x"})

    def test_a_status_event_carries_details(self):
        with pytest.raises(ValidationError):
            ToolkitStatusEvent(task_metadata={})

    def test_an_error_rides_only_a_failure(self):
        error = ToolkitError(code="plan_missing", message="No plan.")
        ToolkitResult(state=ToolkitState.FAILED, error=error)
        ToolkitResult(state=ToolkitState.REJECTED, error=error)
        with pytest.raises(ValidationError, match="failed or was rejected"):
            ToolkitResult(state=ToolkitState.COMPLETED, error=error)

    def test_an_error_code_is_never_empty(self):
        with pytest.raises(ValidationError):
            ToolkitError(code="", message="x")

    def test_unknown_fields_are_refused(self):
        with pytest.raises(ValidationError):
            ToolkitStatusEvent(task_metadata={"a": 1}, stage="x")


class TestOutput:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ({"kind": "message", "message": {"parts": [{"kind": "text", "text": "x"}]}}, ToolkitMessageEvent),
            ({"kind": "artifact", "artifact": {"id": "a", "parts": [{"kind": "text", "text": "x"}]}}, ToolkitArtifactEvent),
            ({"kind": "status", "task_metadata": {"usage": {}}}, ToolkitStatusEvent),
            ({"kind": "result", "state": "input-required"}, ToolkitResult),
        ],
    )
    def test_each_value_is_told_apart_by_its_kind(self, value, expected):
        assert isinstance(OUTPUT.validate_python(value), expected)

    def test_the_kind_is_set_by_default_in_python(self):
        assert ToolkitResult(state=ToolkitState.COMPLETED).kind == "result"
