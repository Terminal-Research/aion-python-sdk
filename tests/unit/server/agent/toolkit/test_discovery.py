"""Finding toolkits: what is registered, what serves, and what a broken plugin leaves behind.

A toolkit is found through its ``aion.toolkits`` entry point. Whatever is
wrong with one - it does not import, is not a toolkit, speaks another
protocol version, cannot configure itself, or is unavailable - it never stops
the server: it is logged, and when its URI is known its extension is refused
with ``extension_unavailable``.
"""

from __future__ import annotations

import importlib.metadata
import logging
import sys
from types import SimpleNamespace

import pytest
from a2a.types import Message, Role
from pydantic import BaseModel

from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
from aion.core.runtime.context.extensions import (
    AionRuntimeExtensions,
    ExtensionActivationError,
    MarkerCollector,
    MessagesCollector,
)
from aion.core.toolkit import ENTRY_POINT_GROUP, ToolkitDescriptor, codes
from aion.server.agent.execution.extensions.toolkit import ToolkitTaskHandler, load_toolkit_handlers

from tests.support.scripted_toolkit.contract import ENTRY_POINT, SITE, URI
from tests.support.scripted_toolkit.toolkit import ScriptedToolkit

MODULE = __name__

OTHER_URI = "https://example.invalid/toolkits/other/1.0.0"


class _NoSchema(BaseModel):
    """A payload model that does not say which schema it is."""


FIRST = ScriptedToolkit()
SECOND = ScriptedToolkit(descriptor=ToolkitDescriptor(uri=OTHER_URI, description="other"))
DEFAULT_ON = ScriptedToolkit(
    descriptor=ToolkitDescriptor(uri=OTHER_URI, description="on by default", enabled_by_default=True)
)
NEWER_PROTOCOL = ScriptedToolkit(
    descriptor=ToolkitDescriptor(uri=URI, description="newer", protocol_version="2.0")
)
NO_SCHEMA = ScriptedToolkit(descriptor=ToolkitDescriptor(uri=URI, description="bad", payloads=(_NoSchema,)))
NOT_CONFIGURED = ScriptedToolkit(configure_error=RuntimeError("SCRIPTED_TOKEN is not set"))
UNAVAILABLE = ScriptedToolkit(unavailable="the scripted backend is unreachable")
CLAIMS_DAEMON = ScriptedToolkit(descriptor=ToolkitDescriptor(uri=DAEMON_EXTENSION_URI_V1, description="x"))
NOT_A_TOOLKIT = SimpleNamespace(descriptor="not a descriptor")


def entry_point(attribute: str, name: str = "test") -> importlib.metadata.EntryPoint:
    return importlib.metadata.EntryPoint(name=name, value=f"{MODULE}:{attribute}", group=ENTRY_POINT_GROUP)


def agent(*enabled: str) -> SimpleNamespace:
    return SimpleNamespace(enabled_extensions=list(enabled))


def declared(uri: str) -> SimpleNamespace:
    """A request declaring ``uri``, as the verification pipeline reads one."""
    return SimpleNamespace(
        message=Message(message_id="m-1", role=Role.ROLE_USER, extensions=[uri]),
        metadata={},
        requested_extensions=set(),
        call_context=None,
    )


def refusal(registry, uri: str) -> ExtensionActivationError:
    with pytest.raises(ExtensionActivationError) as error:
        AionRuntimeExtensions.collect(declared(uri), registry.get_all())
    return error.value


@pytest.fixture(autouse=True)
def fresh_toolkits():
    for toolkit in (FIRST, NOT_CONFIGURED, UNAVAILABLE):
        toolkit.configured = 0


async def test_an_enabled_toolkit_is_registered_configured_and_served(registry) -> None:
    handlers = await load_toolkit_handlers(agent(URI), entry_points=[entry_point("FIRST")])

    assert [type(handler) for handler in handlers] == [ToolkitTaskHandler]
    assert handlers[0].toolkit is FIRST and handlers[0].uri == URI
    assert FIRST.configured == 1
    descriptor = next(item for item in registry.get_all() if item.uri == URI)
    assert descriptor.active and descriptor.unavailable_reason is None
    assert isinstance(descriptor.collector, MessagesCollector)
    assert descriptor.description == FIRST.descriptor.description
    assert registry.unservable_reason(URI) is None


async def test_an_installed_toolkit_is_known_but_not_exposed_until_enabled(registry) -> None:
    handlers = await load_toolkit_handlers(agent(), entry_points=[entry_point("FIRST")])

    assert handlers == []
    assert FIRST.configured == 0
    assert URI in {item.uri for item in registry.get_all()}
    assert URI not in {item.uri for item in registry.get_advertised()}
    assert "enabled_extensions" in str(refusal(registry, URI))


async def test_a_toolkit_on_by_default_needs_no_enabling(registry) -> None:
    handlers = await load_toolkit_handlers(agent(), entry_points=[entry_point("DEFAULT_ON")])

    assert [handler.uri for handler in handlers] == [OTHER_URI]
    assert OTHER_URI in {item.uri for item in registry.get_advertised()}


async def test_a_toolkit_that_does_not_import_is_skipped_and_named(registry, caplog) -> None:
    broken = importlib.metadata.EntryPoint(
        name="broken", value="tests.support.no_such_module:toolkit", group=ENTRY_POINT_GROUP
    )

    with caplog.at_level(logging.WARNING):
        handlers = await load_toolkit_handlers(
            agent(URI), entry_points=[broken, entry_point("FIRST")]
        )

    assert [handler.uri for handler in handlers] == [URI]
    assert "'broken' could not be loaded" in caplog.text


async def test_an_object_that_is_not_a_toolkit_is_skipped(registry, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        handlers = await load_toolkit_handlers(agent(), entry_points=[entry_point("NOT_A_TOOLKIT", "odd")])

    assert handlers == []
    assert "'odd'" in caplog.text and "not a toolkit" in caplog.text


@pytest.mark.parametrize(
    ("attribute", "reason"),
    [
        ("NEWER_PROTOCOL", "toolkit protocol '2.0'"),
        ("NO_SCHEMA", "does not declare SCHEMA_URI, EVENT_TYPE"),
        ("NOT_CONFIGURED", "SCRIPTED_TOKEN is not set"),
        ("UNAVAILABLE", "the scripted backend is unreachable"),
    ],
)
async def test_a_toolkit_that_cannot_serve_is_refused_with_its_reason(
    registry, caplog, attribute, reason
) -> None:
    with caplog.at_level(logging.WARNING):
        handlers = await load_toolkit_handlers(agent(URI), entry_points=[entry_point(attribute, "named")])

    assert handlers == []
    assert reason in caplog.text and "'named'" in caplog.text
    error = refusal(registry, URI)
    assert error.code == codes.EXTENSION_UNAVAILABLE
    assert reason in str(error)
    assert URI not in {item.uri for item in registry.get_advertised()}


async def test_an_unusable_payload_model_is_not_collected(registry) -> None:
    await load_toolkit_handlers(agent(URI), entry_points=[entry_point("NO_SCHEMA")])

    descriptor = next(item for item in registry.get_all() if item.uri == URI)
    assert isinstance(descriptor.collector, MarkerCollector)


async def test_a_toolkit_cannot_take_an_extension_the_server_has(registry, caplog) -> None:
    before = next(item for item in registry.get_all() if item.uri == DAEMON_EXTENSION_URI_V1)

    with caplog.at_level(logging.WARNING):
        handlers = await load_toolkit_handlers(
            agent(DAEMON_EXTENSION_URI_V1), entry_points=[entry_point("CLAIMS_DAEMON")]
        )

    assert handlers == []
    assert "already has" in caplog.text
    assert next(item for item in registry.get_all() if item.uri == DAEMON_EXTENSION_URI_V1) is before


async def test_two_toolkits_cannot_share_a_uri(registry, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        handlers = await load_toolkit_handlers(
            agent(URI), entry_points=[entry_point("FIRST", "one"), entry_point("UNAVAILABLE", "two")]
        )

    assert [handler.toolkit for handler in handlers] == [FIRST]
    assert "'two' claims the extension" in caplog.text


async def test_discovery_can_run_again_in_the_same_process(registry) -> None:
    """A server built twice in one process finds its toolkits twice."""
    await load_toolkit_handlers(agent(URI), entry_points=[entry_point("FIRST")])

    handlers = await load_toolkit_handlers(agent(URI), entry_points=[entry_point("FIRST")])

    assert [handler.uri for handler in handlers] == [URI]


async def test_the_test_toolkit_is_installed_by_its_site_directory(registry, monkeypatch) -> None:
    """What the scenarios rely on: its directory on the path is an installation."""
    monkeypatch.setattr(sys, "path", [str(SITE), *sys.path])

    names = {item.name for item in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)}
    handlers = await load_toolkit_handlers(agent(URI))

    assert ENTRY_POINT in names
    assert [handler.uri for handler in handlers] == [URI]
