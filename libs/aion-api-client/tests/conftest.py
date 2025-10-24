import warnings
import datetime

import jwt
import pytest
from aion.shared.config import AgentConfig

from aion.api.http import AionJWTManager
from aion.api.http.jwt_manager import Token

# Suppress deprecation warnings emitted by third-party dependencies which are
# not relevant to the behaviour under test.
warnings.filterwarnings("ignore", category=DeprecationWarning)


@pytest.fixture
def anyio_backend():
    """Restrict AnyIO tests to the asyncio backend to avoid trio dependency."""
    return "asyncio"


class DummyJWTManager(AionJWTManager):
    """Mock JWT manager for testing purposes."""

    def __init__(self, token: str = None) -> None:
        super().__init__()
        if token:
            self._token = Token.from_jwt(token)
        else:
            # Create a default valid token
            exp = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(minutes=5)
            default_token = jwt.encode({"exp": int(exp.timestamp())}, "secret", algorithm="HS256")
            self._token = Token.from_jwt(default_token)

    async def _refresh_token(self) -> None:
        """Mock refresh token implementation."""
        return None


@pytest.fixture
def valid_jwt_token():
    """Create a valid JWT token for testing."""
    exp = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(minutes=5)
    return jwt.encode({"exp": int(exp.timestamp())}, "secret", algorithm="HS256")


@pytest.fixture
def dummy_jwt_manager(valid_jwt_token):
    """Create a dummy JWT manager with a valid token."""
    return DummyJWTManager(valid_jwt_token)


@pytest.fixture
def agent_config() -> AgentConfig:
    return AgentConfig(
        path="test_path.py:test_agent",
        name="test-agent",
        port=10000,
    )
