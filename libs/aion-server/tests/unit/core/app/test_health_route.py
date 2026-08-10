"""Tests for the health route's reporting of the platform connection."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aion.server.constants import HEALTH_CHECK_URL
from aion.server.core.app.api.routes import AionExtraHTTPRoutes


def _client(lifespan=None) -> TestClient:
    app = FastAPI()
    AionExtraHTTPRoutes(SimpleNamespace(config=SimpleNamespace(configuration={})),
                        lifespan=lifespan).register(app)
    return TestClient(app)


def test_health_reports_the_platform_connection():
    """Operators had no way to see the platform link short of reading the logs."""
    lifespan = SimpleNamespace(platform_connection_state={
        "status": "connected", "reconnects": 2, "lastError": "ConnectionResetError: boom"})

    response = _client(lifespan).get(HEALTH_CHECK_URL)

    assert response.status_code == 200
    assert response.json()["platform"]["status"] == "connected"
    assert response.json()["platform"]["reconnects"] == 2


def test_health_stays_ok_while_the_platform_link_is_down():
    """A reconnecting agent still serves A2A, so 503 would only cause a restart loop."""
    lifespan = SimpleNamespace(platform_connection_state={
        "status": "disconnected", "reconnects": 5, "lastError": "OSError: unreachable"})

    response = _client(lifespan).get(HEALTH_CHECK_URL)

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["platform"]["status"] == "disconnected"


def test_health_without_a_lifespan_keeps_its_original_shape():
    """The route is mounted standalone in tests and by callers that pass no lifespan."""
    response = _client().get(HEALTH_CHECK_URL)

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
