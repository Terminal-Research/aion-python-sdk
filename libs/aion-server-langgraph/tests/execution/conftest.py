import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_aion_runtime_context():
    """Patch get_aion_runtime_context to return None for all executor tests.

    LangGraphExecutor now retrieves the pre-built context from execution scope
    (set at server level by AionAgentRequestExecutor). In unit tests, there is
    no server-level scope, so we patch the helper to return None.
    """
    with patch(
        "aion.langgraph.server.execution.langgraph_executor.get_aion_runtime_context",
        return_value=None,
    ):
        yield
