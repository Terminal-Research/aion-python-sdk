"""Tests for the public agent configuration discovery route."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aion.server.constants import CONFIGURATION_FILE_URL
from aion.server.core.app.api.routes import AionExtraHTTPRoutes


def test_configuration_route_omits_null_secret_metadata():
    """Secret fields publish only metadata supported by their wire contract."""
    agent = SimpleNamespace(
        config=SimpleNamespace(
            configuration={
                "api_key": {
                    "type": "secret",
                    "description": "Protected API credential",
                    "nullable": True,
                }
            }
        )
    )
    app = FastAPI()
    AionExtraHTTPRoutes(agent).register(app)

    response = TestClient(app).get(CONFIGURATION_FILE_URL)

    assert response.status_code == 200
    assert response.json()["configuration"]["api_key"] == {
        "description": "Protected API credential",
        "required": False,
        "nullable": True,
        "type": "secret",
    }
