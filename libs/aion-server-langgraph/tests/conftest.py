import pytest

from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter

TASK_ID = "task-123"
CONTEXT_ID = "ctx-456"


@pytest.fixture
def task_id():
    return TASK_ID


@pytest.fixture
def context_id():
    return CONTEXT_ID


@pytest.fixture
def converter(task_id, context_id):
    return LangGraphA2AConverter(task_id=task_id, context_id=context_id)
