import pytest
from unittest.mock import AsyncMock, patch

from aion.core.runtime.context.registry import AionRuntimeContextRegistry


@pytest.fixture(autouse=True)
def mock_aion_runtime_context():
    """Patch AionRuntimeContextRegistry to return None for all executor tests.

    LangGraphExecutor retrieves the runtime context via AionRuntimeContextRegistry.
    In unit tests there is no registered provider, so we patch the registry
    directly to return None.
    """
    with patch.object(AionRuntimeContextRegistry, "aget_current_context", new=AsyncMock(return_value=None)):
        yield
