"""Tests for the agent health route."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aion.server.constants import HEALTH_CHECK_URL
from aion.server.core.app.api.routes import AionExtraHTTPRoutes


def _client() -> TestClient:
    app = FastAPI()
    AionExtraHTTPRoutes(
        SimpleNamespace(config=SimpleNamespace(configuration={}))).register(app)
    return TestClient(app)


def test_health_reports_only_this_agent_process():
    """The platform link belongs to the deployment, not to any one agent process.

    It used to be reported here from a socket each agent opened for itself, which
    answered "connected" even when the deployment version had failed to register
    and the agent could receive nothing.
    """
    response = _client().get(HEALTH_CHECK_URL)

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
