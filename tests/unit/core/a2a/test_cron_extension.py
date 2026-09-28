"""Cron wire and activation contracts shared with the Scala producer."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct
from pydantic import ValidationError

from aion.core.a2a.extensions.cron import CronExtensionV1
from aion.core.constants.a2a import CRON_EXTENSION_URI_V1
from aion.core.runtime.context.extensions.descriptors import ExtensionActivationError
from aion.core.runtime.context.extensions.pipeline import AionRuntimeExtensions
from aion.core.runtime.context.extensions.registry import aion_a2a_extension_registry

FIXTURES = Path(__file__).parents[1] / "fixtures" / "a2a"


def _payload(name: str = "recurring") -> dict:
    """Load a verbatim copy of the backend wire fixture."""
    return json.loads((FIXTURES / f"cron-{name}.json").read_text())


@pytest.mark.parametrize("name", ["recurring", "one-time"])
def test_wire_contract(name: str) -> None:
    """UUIDs, aliases, UTC timestamps and schedule shapes agree across clients."""
    data = _payload(name)
    payload = CronExtensionV1.model_validate(data)
    assert payload.model_dump(mode="json", by_alias=True, exclude_none=True) == data


@pytest.mark.parametrize(("path", "value"), [
    (("attachmentId",), "invalid"),
    (("schedule", "type"), "unknown"),
    (("schedule", "cronExpression"), None),
    (("schedule", "cronExpression"), "* * *"),
    (("schedule", "timezone"), "Not/AZone"),
    (("schedule", "timezone"), "+02:00"),
    (("schedule", "description"), "  "),
    (("sentAt",), "2026-09-28T09:00:03-07:00"),
    (("scheduledAt",), "not-a-timestamp"),
])
def test_invalid_metadata_is_rejected(path: tuple[str, ...], value: object) -> None:
    """Bad scheduling context must fail validation, not become active."""
    data = _payload()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        CronExtensionV1.model_validate(data)


def test_one_time_rejects_expression_and_required_ids_cannot_be_missing() -> None:
    """Do not silently reinterpret a contradictory or incomplete firing."""
    data = _payload("one-time")
    data["schedule"]["cronExpression"] = "0 9 * * *"
    with pytest.raises(ValidationError):
        CronExtensionV1.model_validate(data)
    data = _payload()
    del data["occurrenceId"]
    with pytest.raises(ValidationError):
        CronExtensionV1.model_validate(data)


@pytest.mark.parametrize("explicit", [False, True])
def test_metadata_activation_exposes_typed_payload(explicit: bool) -> None:
    """Detected metadata and explicit headers activate the same verified data."""
    context = SimpleNamespace(
        message=None,
        requested_extensions={CRON_EXTENSION_URI_V1} if explicit else set(),
        metadata={CRON_EXTENSION_URI_V1: ParseDict(_payload(), Struct())},
    )
    extensions = AionRuntimeExtensions.collect(
        context, aion_a2a_extension_registry.get_all()
    )
    assert extensions.is_active(CRON_EXTENSION_URI_V1)
    assert isinstance(extensions.get(CRON_EXTENSION_URI_V1), CronExtensionV1)


def test_support_alone_does_not_activate_ordinary_requests() -> None:
    """Advertising Cron must not impose metadata on interactive messages."""
    context = SimpleNamespace(message=None, requested_extensions=set(), metadata={})
    extensions = AionRuntimeExtensions.collect(
        context, aion_a2a_extension_registry.get_all()
    )
    assert not extensions.is_active(CRON_EXTENSION_URI_V1)
    context.requested_extensions = {CRON_EXTENSION_URI_V1}
    with pytest.raises(ExtensionActivationError):
        AionRuntimeExtensions.collect(context, aion_a2a_extension_registry.get_all())
