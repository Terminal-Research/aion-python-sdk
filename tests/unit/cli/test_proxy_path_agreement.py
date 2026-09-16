"""The CLI must advertise the same agent address the proxy actually serves.

These two helpers live in different packages and used to build the address
independently. They drifted: the proxy's builder produced an agent root without
a trailing slash and the CLI's produced one with, so the address printed at
startup and the address embedded in the OpenAPI schema were different strings
for one endpoint. Nothing caught it, because a caller who guessed the other
spelling was redirected rather than refused.
"""

import pytest

from aion.proxy.constants import build_agent_path
from aion.cli.utils.proxy_utils import format_agent_proxy_path


@pytest.mark.parametrize("path", [
    "",
    "docs",
    "openapi.json",
    ".well-known/agent-card.json",
    "/.well-known/agent-card.json",
    "health/",
])
def test_the_cli_advertises_what_the_proxy_serves(path):
    """One address per endpoint, whichever side of the CLI/proxy line asks."""
    assert format_agent_proxy_path("my-agent", path) == build_agent_path("my-agent", path)


def test_an_agent_root_carries_no_trailing_slash():
    """Pins the canonical spelling, which an OpenAPI ``servers`` entry needs.

    Swagger UI appends operation paths that already begin with a slash, so a
    base ending in one yields '//health/'.
    """
    assert build_agent_path("my-agent") == "/agents/my-agent"


def test_a_leading_slash_on_the_sub_path_does_not_double_up():
    """Callers pass both spellings; neither may produce '//'."""
    assert build_agent_path("my-agent", "/docs") == build_agent_path("my-agent", "docs")
