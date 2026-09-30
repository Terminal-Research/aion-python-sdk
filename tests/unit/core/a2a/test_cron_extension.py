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


def _payload() -> dict:
    """Load a verbatim copy of the backend wire fixture."""
    return json.loads((FIXTURES / "cron.json").read_text())


def test_wire_contract() -> None:
    """Only aliased UTC timestamps cross the wire, for every schedule kind."""
    data = _payload()
    payload = CronExtensionV1.model_validate(data)
    assert payload.model_dump(mode="json", by_alias=True, exclude_none=True) == data
    assert set(data) == {"scheduledAt", "sentAt"}


@pytest.mark.parametrize("field", ["scheduledAt", "sentAt"])
@pytest.mark.parametrize("value", [
    None,
    0,
    "not-a-timestamp",
    "2026-09-28T09:00:03-07:00",
    "2026-09-28T16:00:03+00:00",
    "2026-09-28T16:00:03",
])
def test_invalid_metadata_is_rejected(field: str, value: object) -> None:
    """Both timestamps must be valid UTC-Z strings, not local times or epochs."""
    data = _payload()
    data[field] = value
    with pytest.raises(ValidationError):
        CronExtensionV1.model_validate(data)


@pytest.mark.parametrize("field", ["scheduledAt", "sentAt"])
def test_both_timestamps_are_required(field: str) -> None:
    """A receiver must not invent either timestamp when one is missing."""
    data = _payload()
    del data[field]
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


def test_invalid_timing_cannot_activate_extension() -> None:
    """Malformed metadata is rejected before the receiver executes work."""
    data = _payload()
    data["sentAt"] = "not-a-timestamp"
    context = SimpleNamespace(
        message=None,
        requested_extensions={CRON_EXTENSION_URI_V1},
        metadata={CRON_EXTENSION_URI_V1: ParseDict(data, Struct())},
    )
    with pytest.raises(ExtensionActivationError):
        AionRuntimeExtensions.collect(context, aion_a2a_extension_registry.get_all())


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
