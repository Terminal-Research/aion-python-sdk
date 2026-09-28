"""Tests that the platform connection follows deployment version registration."""

from __future__ import annotations

import pytest

import aion.cli.handlers.serve as serve


@pytest.fixture
def platform(monkeypatch):
    """Replace the platform connection with a recorder, and drive registration."""
    record = {"registered": True, "started": [], "stopped": [], "managers": 0}

    class FakeRegistrationService:
        async def execute(self, agent_ids):
            return record["registered"]

    class FakeManager:
        def __init__(self, ws_transport_factory):
            record["managers"] += 1

    class FakeWebSocketService:
        def __init__(self, websocket_manager):
            self._manager = websocket_manager

        async def start_connection(self):
            record["started"].append(self._manager)

        async def stop_connection(self):
            record["stopped"].append(self._manager)

    # Credentials decide whether there is a platform to reach at all, and a
    # developer machine running the suite has none.
    monkeypatch.setattr(serve.api_settings, "client_id", "client-id")
    monkeypatch.setattr(serve.api_settings, "client_secret", "client-secret")
    monkeypatch.setattr(
        serve, "AionDeploymentRegisterVersionService", FakeRegistrationService)
    monkeypatch.setattr(serve, "AionWebSocketManager", FakeManager)
    monkeypatch.setattr(serve, "WebsocketTransportFactory", lambda **kwargs: None)
    monkeypatch.setattr(
        serve.aion_services, "AionWebSocketService", FakeWebSocketService)
    return record


async def test_no_connection_when_the_version_is_not_registered(platform):
    """An unregistered deployment has nothing to announce.

    Connecting anyway reported a healthy link for agents the platform would never
    route traffic to, immediately below the warning saying registration failed.
    """
    platform["registered"] = False

    await serve.ServeHandler()._link_to_platform(["agent"])

    assert platform["managers"] == 0
    assert platform["started"] == []


async def test_nothing_is_attempted_without_credentials(platform, monkeypatch):
    """Serving locally is a mode of running, not a platform outage.

    Registering retries transient failures forever, and with no credentials every
    attempt fails identically forever, so a local run would spend its life warning
    about a value that is missing on purpose.
    """
    monkeypatch.setattr(serve.api_settings, "client_id", None)
    registrations = []
    monkeypatch.setattr(
        serve, "AionDeploymentRegisterVersionService",
        lambda: registrations.append(1))

    await serve.ServeHandler()._link_to_platform(["agent"])

    assert registrations == []
    assert platform["started"] == []


async def test_connection_opens_once_the_version_is_registered(platform):
    """Registration is what makes the connection meaningful, so it gates it."""
    handler = serve.ServeHandler()

    await handler._link_to_platform(["agent"])

    assert platform["managers"] == 1
    assert len(platform["started"]) == 1
    assert handler._websocket_manager is not None


async def test_shutdown_closes_the_connection(platform):
    """The socket outlives its opening task, so shutdown has to close it."""
    handler = serve.ServeHandler()
    await handler._link_to_platform(["agent"])

    await handler._stop_platform_link()

    assert len(platform["stopped"]) == 1
    assert handler._websocket_manager is None


async def test_shutdown_drops_a_registration_still_retrying(platform):
    """Registration retries for about a minute; Ctrl-C must not wait it out."""
    import asyncio

    async def never_finishes(agent_ids):
        await asyncio.Event().wait()

    handler = serve.ServeHandler()
    handler._platform_link_task = asyncio.create_task(never_finishes(["agent"]))
    await asyncio.sleep(0)

    await handler._stop_platform_link()

    assert handler._platform_link_task is None
    assert platform["started"] == []
