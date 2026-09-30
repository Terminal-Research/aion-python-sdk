"""Welcome admission, opt-in, and user-text precedence on actual wire messages."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from a2a.types import Message
from google.protobuf.json_format import ParseDict

from aion.core.a2a.extensions.welcome_message import WelcomeRequestPayload
from aion.core.constants.a2a import (
    WELCOME_MESSAGE_EXTENSION_URI_V1 as URI,
    WELCOME_REQUEST_PAYLOAD_SCHEMA_V1 as SCHEMA,
)
from aion.core.runtime.context.extensions.descriptors import ExtensionActivationError
from aion.core.runtime.context.extensions.pipeline import AionRuntimeExtensions
from aion.core.runtime.context.extensions.registry import aion_a2a_extension_registry


def _part():
    return {"data": {"type": "welcome-request"}, "metadata": {URI: {"schema": SCHEMA}}}


def _collect(parts=None, *, enabled=True, header=False, message=True, metadata=None):
    context = SimpleNamespace(
        message=ParseDict({
            "messageId": "welcome-request", "contextId": "existing-context",
            "role": "ROLE_USER", "parts": [_part()] if parts is None else parts,
            "extensions": [URI] if message else [],
        }, Message()),
        requested_extensions={URI} if header else set(),
        metadata=metadata or {},
    )
    descriptors = [
        replace(d, active=enabled) if d.uri == URI else d
        for d in aion_a2a_extension_registry.get_all()
    ]
    return AionRuntimeExtensions.collect(context, descriptors)


def test_inactive_by_default():
    descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == URI)
    assert not descriptor.active
    with pytest.raises(ExtensionActivationError):
        _collect(enabled=False)


@pytest.mark.parametrize("header,message", [(True, False), (False, True), (True, True)])
def test_explicit_activation_exposes_typed_payload(header, message):
    extensions = _collect(header=header, message=message)
    assert extensions.is_active(URI)
    assert isinstance(extensions.get(URI), WelcomeRequestPayload)
    assert extensions.get(URI).type == "welcome-request"


def test_payload_and_metadata_do_not_implicitly_activate():
    assert not _collect(message=False).is_active(URI)
    assert not _collect(message=False, metadata={URI: {}}).is_active(URI)


@pytest.mark.parametrize("parts", [
    [], [{"data": {"type": "welcome-request"}}],
    [{"data": {"type": "other"}, "metadata": {URI: {"schema": SCHEMA}}}],
    [{"data": {"type": "welcome-request"}, "metadata": {URI: {"schema": "wrong"}}}],
    [{"data": {"type": "welcome-request"}, "metadata": {URI: "wrong"}}],
    [{"text": "", "metadata": {URI: {"schema": SCHEMA}}}],
    [_part(), _part()],
])
def test_malformed_request_rejected_before_execution(parts):
    with pytest.raises(ExtensionActivationError):
        _collect(parts)


@pytest.mark.parametrize("enabled", [True, False])
def test_actual_user_text_wins_even_when_welcome_invalid(enabled):
    extensions = _collect([{"text": "Answer my question"}], enabled=enabled)
    assert not extensions.is_active(URI)


def test_other_distribution_parts_remain_accepted():
    assert _collect([_part(), {"data": {"provider": "telegram"}}]).is_active(URI)
