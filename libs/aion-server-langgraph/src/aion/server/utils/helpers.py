from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

from a2a.types import UnsupportedOperationError
from a2a.utils.errors import ServerError

from aion.server.types import AgentNotFoundError

if TYPE_CHECKING:
    from aion.server.langgraph.agent import BaseAgent


logger = logging.getLogger(__name__)


def validate_operation(
    expression: Callable[[], bool] | bool | None,
    error_message: str | None = None
):
    if isinstance(expression, bool):
        result = expression
    elif callable(expression):
        result = expression()
    else:
        result = False

    if not result:
        final_message = error_message or str(expression)
        logger.error(f'Unsupported Operation: {final_message}')
        raise ServerError(UnsupportedOperationError(message=final_message))


def validate_agent_id(agent_id: str | None = None) -> BaseAgent:
    from aion.server.langgraph.agent import agent_manager
    if not agent_id:
        agent = agent_manager.get_first_agent()
    else:
        agent = agent_manager.get_agent(agent_id)

    if not agent:
        raise ServerError(AgentNotFoundError.with_id(agent_id))
    return agent


