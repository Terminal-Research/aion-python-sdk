"""The plugin contract's own helpers."""

import pytest

from aion.core.exceptions import AionError
from aion.core.toolkit import (
    ContextArtifact,
    PROTOCOL_VERSION,
    Artifact,
    TextPart,
    ToolkitContext,
    ToolkitDescriptor,
    ToolkitRejection,
    codes,
    protocol_compatible,
)


@pytest.mark.parametrize(
    "toolkit, server, expected",
    [
        ("1.0", "1.0", True),
        ("1.0", "1.3", True),
        ("1.4", "1.3", False),
        ("2.0", "1.9", False),
        ("1", "1.0", False),
        ("one.two", "1.0", False),
    ],
)
def test_protocol_compatibility(toolkit, server, expected):
    assert protocol_compatible(toolkit, server) is expected


def test_a_toolkit_built_for_this_server_is_compatible():
    assert protocol_compatible(PROTOCOL_VERSION)


def test_a_rejection_is_an_aion_error_with_a_public_reason():
    rejection = ToolkitRejection("invalid_payload", "The base ref is not valid.")
    assert isinstance(rejection, AionError)
    assert rejection.public_reason == "The base ref is not valid."
    assert rejection.retryable is False
    with pytest.raises(ValueError):
        ToolkitRejection("", "x")


def test_every_common_code_has_a_fixed_retry_hint():
    assert set(codes.RETRYABLE) == {
        codes.CONTEXT_BUSY,
        codes.EXTENSION_UNAVAILABLE,
        codes.CALLER_NOT_ALLOWED,
        codes.INVALID_PAYLOAD,
        codes.SERVER_SHUTDOWN,
        codes.TASK_ABANDONED,
        codes.INTERNAL_ERROR,
    }
    assert codes.RETRYABLE[codes.CONTEXT_BUSY] is True
    assert codes.RETRYABLE[codes.CALLER_NOT_ALLOWED] is False
    assert codes.RETRYABLE[codes.INVALID_PAYLOAD] is False
    assert codes.RETRYABLE[codes.TASK_ABANDONED] is True


async def test_a_context_finds_artifacts_through_its_finder():
    found = ContextArtifact(artifact=Artifact(id="spec", parts=[TextPart(text="plan")]), task_id="t1", version=2)

    async def finder(artifact_id):
        return found if artifact_id == "spec" else None

    context = ToolkitContext(task_id="t2", context_id="c", artifact_finder=finder)
    assert await context.find_artifact("spec") is found
    assert await context.find_artifact("other") is None
    assert await ToolkitContext(task_id="t", context_id="c").find_artifact("spec") is None
