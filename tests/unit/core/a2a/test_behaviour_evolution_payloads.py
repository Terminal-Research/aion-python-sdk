"""Tests for the behaviour-evolution progress-event payloads.

These schema-tagged payloads are what the improver converts its toolkit events
into and streams while a run is in flight. The tests pin the camelCase wire
shape, the schema URIs, and the optional-field behaviour consumers rely on.
"""

import pytest
from pydantic import ValidationError

from aion.core.a2a.extensions.behaviour_evolution import (
    EvolutionAgentMessagePayload,
    EvolutionCommandCompletedPayload,
    EvolutionCommandStartedPayload,
    EvolutionDirectiveEventPayload,
    ModelPreferences,
    RunLimits,
    TargetContext,
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


def _directive(**overrides):
    kwargs = dict(
        target=TargetContext(repo_url="https://github.com/acme/service.git", base_ref="HEAD"),
        kind="feature",
        mode="advisory",
    )
    kwargs.update(overrides)
    return EvolutionDirectiveEventPayload(**kwargs)


class TestDirectiveModelPreferences:
    """Per-run model tuning on the directive. Optional, so a caller that omits
    it keeps whatever the deployment resolves on its own."""

    def test_absent_when_unset(self):
        assert "model" not in _dump(_directive())

    def test_camel_case_wire_shape(self):
        dumped = _dump(
            _directive(
                model=ModelPreferences(
                    name="gpt-5.1-codex", reasoning_effort="high", context_window=128000
                )
            )
        )
        assert dumped["model"] == {
            "name": "gpt-5.1-codex",
            "reasoningEffort": "high",
            "contextWindow": 128000,
        }

    def test_round_trips_from_camel_case(self):
        payload = EvolutionDirectiveEventPayload.model_validate(
            {
                "target": {"repoUrl": "https://github.com/acme/service.git", "baseRef": "HEAD"},
                "kind": "feature",
                "mode": "advisory",
                "model": {"reasoningEffort": "low"},
            }
        )
        assert payload.model.reasoning_effort == "low"
        assert payload.model.name is None
        assert payload.model.context_window is None


class TestDirectiveBranchStrategy:
    def test_absent_when_unset(self):
        assert "branchStrategy" not in _dump(_directive())

    def test_camel_case_wire_shape(self):
        dumped = _dump(_directive(branch_strategy="pull-request"))
        assert dumped["branchStrategy"] == "pull-request"

    def test_rejects_unknown_strategy(self):
        with pytest.raises(ValidationError):
            _directive(branch_strategy="direct-push")


class TestDirectiveRunLimits:
    """Resource ceilings the caller sets for its own run. Optional, and unlike
    `model` there is no deployment-side value that overrides them."""

    def test_absent_when_unset(self):
        assert "limits" not in _dump(_directive())

    def test_camel_case_wire_shape(self):
        dumped = _dump(
            _directive(
                limits=RunLimits(
                    max_total_tokens=120000,
                    op_timeout=30.0,
                    network_timeout=45.5,
                    codex_timeout=600.0,
                )
            )
        )
        assert dumped["limits"] == {
            "maxTotalTokens": 120000,
            "opTimeout": 30.0,
            "networkTimeout": 45.5,
            "codexTimeout": 600.0,
        }

    def test_round_trips_from_camel_case(self):
        payload = EvolutionDirectiveEventPayload.model_validate(
            {
                "target": {"repoUrl": "https://github.com/acme/service.git", "baseRef": "HEAD"},
                "kind": "feature",
                "mode": "advisory",
                "limits": {"maxTotalTokens": 50000, "codexTimeout": 300},
            }
        )
        assert payload.limits.max_total_tokens == 50000
        assert payload.limits.codex_timeout == 300
        assert payload.limits.op_timeout is None
        assert payload.limits.network_timeout is None
