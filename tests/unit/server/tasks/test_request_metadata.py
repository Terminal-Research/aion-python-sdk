"""Which metadata keys only a request carries.

``aion db strip-request-metadata`` removes these keys from stored tasks, so the
set has to hold every inbound extension payload and nothing the server writes
into a task itself.
"""

from __future__ import annotations

from aion.core.a2a.enums import A2AMetadataKey
from aion.core.constants.a2a import (
    CRON_EXTENSION_URI_V1,
    DAEMON_EXTENSION_URI_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    TRACEABILITY_EXTENSION_URI_V1,
)
from aion.server.tasks.request_metadata import request_metadata_keys

TOOLKIT_URI = "https://example.invalid/toolkits/scripted/1.0.0"
"""A toolkit keeps its object in the task under its URI; its payloads ride on the message."""


def test_every_payload_a_request_carries_in_its_metadata_is_a_request_key() -> None:
    assert set(request_metadata_keys()) == {
        CRON_EXTENSION_URI_V1,
        DAEMON_EXTENSION_URI_V1,
        DISTRIBUTION_EXTENSION_URI_V1,
        TRACEABILITY_EXTENSION_URI_V1,
    }


def test_no_key_the_server_writes_into_a_task_is_a_request_key() -> None:
    """The route, a settlement and a toolkit's object survive the clean-up."""
    server_keys = {
        A2AMetadataKey.ROUTED_EXTENSION.value,
        A2AMetadataKey.SETTLED_REASON.value,
        TOOLKIT_URI,
    }

    assert server_keys.isdisjoint(request_metadata_keys())
