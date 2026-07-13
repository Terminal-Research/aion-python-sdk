"""Tests for evolution directive extraction/validation (toolkit-free)."""

from types import SimpleNamespace

import pytest
from a2a.types import Message, Part, Role

from aion.core.a2a.extensions.behaviour_evolution import (
    EvolutionDirectiveEventPayload,
    TargetContext,
)
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1,
)
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from aion.core.runtime.context.models import Event
from aion.server.agent.execution.extensions.evolution.directive import (
    DirectiveError,
    parse_directive,
)

REPO_URL = "https://github.com/acme/target-agent.git"


def _payload() -> EvolutionDirectiveEventPayload:
    return EvolutionDirectiveEventPayload(
        target=TargetContext(repo_url=REPO_URL, base_ref="HEAD", target_version_id="v-1"),
        kind="feature",
        mode="advisory",
    )


def _event(kind: str = BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1, payload=None) -> Event:
    return Event(
        kind=kind,
        id="ev-1",
        source="aion://control-plane/reflection",
        payload=payload,
        raw=None,
    )


def _runtime_ctx(event: Event | None):
    verified = {BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1: event} if event is not None else {}
    return SimpleNamespace(extensions=AionRuntimeExtensions(verified))


def _request_ctx(text: str | None = "Append a friendly sentence to README.md."):
    parts = [Part(text=text)] if text is not None else [Part()]
    msg = Message(message_id="msg-1", role=Role.ROLE_USER, parts=parts)
    return SimpleNamespace(message=msg)


class TestParseDirective:
    def test_happy_path_returns_instruction_and_payload(self):
        payload = _payload()
        parsed = parse_directive(_request_ctx(), _runtime_ctx(_event(payload=payload)))

        assert parsed.instruction == "Append a friendly sentence to README.md."
        assert parsed.payload is payload
        assert parsed.payload.target.repo_url == REPO_URL

    def test_instruction_is_first_nonempty_text_part(self):
        msg = Message(
            message_id="msg-1",
            role=Role.ROLE_USER,
            parts=[Part(text="  "), Part(text="Fix the greeting."), Part(text="ignored")],
        )
        ctx = SimpleNamespace(message=msg)

        parsed = parse_directive(ctx, _runtime_ctx(_event(payload=_payload())))

        assert parsed.instruction == "Fix the greeting."

    def test_missing_runtime_context_rejected(self):
        with pytest.raises(DirectiveError, match="runtime context"):
            parse_directive(_request_ctx(), None)

    def test_missing_directive_event_rejected(self):
        with pytest.raises(DirectiveError, match="no evolution event"):
            parse_directive(_request_ctx(), _runtime_ctx(None))

    def test_non_directive_event_type_rejected(self):
        event = _event(kind=BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1, payload=_payload())
        with pytest.raises(DirectiveError, match="unsupported evolution event type"):
            parse_directive(_request_ctx(), _runtime_ctx(event))

    def test_missing_payload_rejected(self):
        with pytest.raises(DirectiveError, match="no directive payload"):
            parse_directive(_request_ctx(), _runtime_ctx(_event(payload=None)))

    def test_missing_instruction_text_rejected(self):
        with pytest.raises(DirectiveError, match="no instruction text"):
            parse_directive(_request_ctx(text=None), _runtime_ctx(_event(payload=_payload())))

    def test_missing_message_rejected(self):
        ctx = SimpleNamespace(message=None)
        with pytest.raises(DirectiveError, match="no instruction text"):
            parse_directive(ctx, _runtime_ctx(_event(payload=_payload())))
