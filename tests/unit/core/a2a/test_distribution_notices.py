"""Distribution notices remain separate from event routing and request context."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from a2a.types import Message
from google.protobuf.json_format import ParseDict
from pydantic import ValidationError

from aion.core.a2a import A2AInbox
from aion.core.a2a.extensions.distribution import (
    NoticePayload,
    extract_distribution_notices,
)
from aion.core.constants.a2a import (
    DISTRIBUTION_EXTENSION_URI_V1,
    DISTRIBUTION_NOTICE_PAYLOAD_SCHEMA_V1,
    EVENT_EXTENSION_URI_V1,
)
from aion.core.runtime.context.builder import AionRuntimeContextBuilder
from aion.core.runtime.context.utils import extract_event

FIXTURE = json.loads((
    Path(__file__).parents[1] / "fixtures/a2a/distribution-notices-1.0.0.json"
).read_text())


@pytest.mark.parametrize("reverse", [False, True])
def test_attachment_only_notice_keeps_event_and_context(reverse: bool) -> None:
    data = copy.deepcopy(FIXTURE["message"])
    if reverse:
        data["parts"].reverse()
    message = ParseDict(data, Message())
    request = SimpleNamespace(
        current_task=None,
        message=message,
        metadata=FIXTURE["requestMetadata"],
        requested_extensions=frozenset(message.extensions),
    )
    context = AionRuntimeContextBuilder.from_request_context(request)
    assert context is not None
    assert context.distribution_extension_payload.distribution.endpoint_type == "Twitter"
    assert context.distribution_extension_payload.behavior.behavior_key == "chat"
    assert context.distribution_extension_payload.environment.name == "staging"
    event = extract_event(A2AInbox(message=message))
    assert event.id == "event-789"
    assert event.payload.message_id == "message-789"
    assert event.payload.context_id == "chat-456"
    assert context.event.payload == event.payload
    assert all(part.WhichOneof("content") == "data" for part in message.parts)
    notices = extract_distribution_notices(context.inbox.message)
    assert [notice.model_dump() for notice in notices] == [FIXTURE["notice"]]
    assert NoticePayload.SCHEMA_URI == FIXTURE["schemaUri"]
    assert DISTRIBUTION_NOTICE_PAYLOAD_SCHEMA_V1 == FIXTURE["schemaUri"]


@pytest.mark.parametrize("code", FIXTURE["otherCodes"])
def test_unknown_codes_do_not_become_events(code: str) -> None:
    data = copy.deepcopy(FIXTURE["message"])
    data["parts"][0]["data"]["code"] = code
    message = ParseDict(data, Message())
    assert extract_distribution_notices(message)[0].code == code
    assert extract_event(A2AInbox(message=message)).payload.message_id == "message-789"


@pytest.mark.parametrize("source", FIXTURE["validSources"])
def test_source_validation_preserves_resource_uri(source: str) -> None:
    payload = NoticePayload.model_validate({**FIXTURE["notice"], "source": source})
    assert payload.source == source


@pytest.mark.parametrize("source", FIXTURE["invalidSources"])
def test_invalid_and_credential_sources_are_rejected(source: str) -> None:
    with pytest.raises(ValidationError):
        NoticePayload.model_validate({**FIXTURE["notice"], "source": source})


@pytest.mark.parametrize("field", ["code", "message"])
def test_blank_required_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        NoticePayload.model_validate({**FIXTURE["notice"], field: "  "})


@pytest.mark.parametrize("variant", ["missing", "event", "text", "invalid", "scalar"])
def test_malformed_optional_notices_cannot_replace_event(variant: str) -> None:
    data = copy.deepcopy(FIXTURE["message"])
    part = data["parts"][0]
    if variant == "missing":
        part.pop("metadata")
    elif variant == "event":
        part["metadata"] = {EVENT_EXTENSION_URI_V1: {"schema": FIXTURE["schemaUri"]}}
    elif variant == "text":
        part.pop("data")
        part["text"] = "a notice is not sender-authored text"
    elif variant == "scalar":
        part["metadata"] = {DISTRIBUTION_EXTENSION_URI_V1: "wrong marker"}
    else:
        part["data"] = {"code": "attachments-disabled"}
    message = ParseDict(data, Message())
    assert extract_distribution_notices(message) == []
    assert extract_event(A2AInbox(message=message)).payload.message_id == "message-789"


def test_absent_message_has_no_notices() -> None:
    assert extract_distribution_notices(None) == []
