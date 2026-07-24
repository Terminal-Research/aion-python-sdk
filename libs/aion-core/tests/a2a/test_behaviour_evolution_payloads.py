"""Tests for the behaviour-evolution progress-event payloads.

These schema-tagged payloads are what the improver converts its toolkit events
into and streams while a run is in flight. The tests pin the camelCase wire
shape, the schema URIs, and the optional-field behaviour consumers rely on.
"""

from aion.core.a2a.extensions.behaviour_evolution import (
    EvolutionAgentMessagePayload,
    EvolutionCommandCompletedPayload,
    EvolutionCommandStartedPayload,
)
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
)


def _dump(payload):
    return payload.model_dump(mode="json", by_alias=True, exclude_none=True)


class TestSchemaUris:
    def test_schema_uris_are_fragments_of_the_extension_uri(self):
        for payload, schema in (
            (EvolutionCommandStartedPayload, BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1),
            (
                EvolutionCommandCompletedPayload,
                BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
            ),
            (EvolutionAgentMessagePayload, BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1),
        ):
            assert payload.SCHEMA_URI == schema
            assert schema.startswith(f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#")
            assert schema.endswith(payload.__name__)


class TestCommandStarted:
    def test_camel_case_wire_shape(self):
        assert _dump(EvolutionCommandStartedPayload(call_id="c1", command="pytest -q")) == {
            "callId": "c1",
            "command": "pytest -q",
        }

    def test_round_trips_from_camel_case(self):
        payload = EvolutionCommandStartedPayload.model_validate(
            {"callId": "c1", "command": "ls"}
        )
        assert payload.call_id == "c1"
        assert payload.command == "ls"


class TestCommandCompleted:
    def test_zero_exit_code_is_kept_under_exclude_none(self):
        # exclude_none must not drop a passing command's exit code (0 is not None).
        dumped = _dump(
            EvolutionCommandCompletedPayload(
                call_id="c1", command="pytest -q", exit_code=0, output="3 passed"
            )
        )
        assert dumped == {
            "callId": "c1",
            "command": "pytest -q",
            "exitCode": 0,
            "output": "3 passed",
            "truncated": False,
        }

    def test_optional_fields_absent_when_unset(self):
        # exit_code/output are None -> dropped; truncated is a bool default -> kept.
        dumped = _dump(EvolutionCommandCompletedPayload(call_id="c1", command="ls"))
        assert dumped == {"callId": "c1", "command": "ls", "truncated": False}

    def test_truncated_flag(self):
        dumped = _dump(
            EvolutionCommandCompletedPayload(
                call_id="c1", command="cat big.log", output="...tail", truncated=True
            )
        )
        assert dumped["truncated"] is True


class TestAgentMessage:
    def test_defaults_to_non_final(self):
        payload = EvolutionAgentMessagePayload(text="working")
        assert payload.final is False
        assert _dump(payload) == {"text": "working", "final": False}

    def test_final_summary(self):
        assert _dump(EvolutionAgentMessagePayload(text="done", final=True)) == {
            "text": "done",
            "final": True,
        }
