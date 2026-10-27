"""A deployment the streaming scenarios of several modules share."""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio

from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant

EVERY_CHUNK_VARIANT = ServeVariant(name="every-chunk", env={"AION_STREAM_DELTA_INTERVAL": "0"})
"""The default deployment with no interval between two chunks of a streamed answer.

By default the server joins the chunks that follow the last one it sent by
less than ``AION_STREAM_DELTA_INTERVAL``, so how many of them reach a client
depends on how fast the agent produced them. With the interval at zero every
chunk goes out as the agent produced it, which is what lets a scenario count
them and read each one's flags.
"""


@pytest.fixture(scope="session")
def every_chunk_server(server_for) -> ServeProcess:
    """A deployment that passes on every chunk of a streamed answer."""
    return server_for(EVERY_CHUNK_VARIANT)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def every_chunk_client(every_chunk_server: ServeProcess) -> AsyncIterator[ScenarioClient]:
    """A client on the deployment that passes on every chunk."""
    scenario_client = await ScenarioClient.connect(
        every_chunk_server.base_url, every_chunk_server.variant.agent_id
    )
    try:
        yield scenario_client
    finally:
        await scenario_client.close()
