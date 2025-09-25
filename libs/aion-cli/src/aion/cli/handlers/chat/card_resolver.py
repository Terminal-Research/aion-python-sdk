import json
from typing import Optional, Any

import httpx
from a2a.client import A2ACardResolver, A2AClientHTTPError, A2AClientJSONError
from a2a.types import AgentCard
from aion.shared.logging import get_logger
from pydantic import ValidationError

logger = get_logger(__name__)


class AionA2ACardResolver(A2ACardResolver):

    def __init__(self, *args, graph_id: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.graph_id = graph_id

        if self.graph_id:
            self.agent_card_path = f"{self.graph_id}/{self.agent_card_path}"

    async def get_agent_card(
            self,
            relative_card_path: str | None = None,
            http_kwargs: dict[str, Any] | None = None,
    ) -> AgentCard:
        """Fetches an agent card from a specified path relative to the base_url.

        If relative_card_path is None, it defaults to the resolver's configured
        agent_card_path (for the public agent card).

        Args:
            relative_card_path: Optional path to the agent card endpoint,
                relative to the base URL. If None, uses the default public
                agent card path.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.get request.

        Returns:
            An `AgentCard` object representing the agent's capabilities.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON
                or validated against the AgentCard schema.
        """
        if relative_card_path is None:
            # Use the default public agent card path configured during initialization
            path_segment = self.agent_card_path
        else:
            path_segment = relative_card_path.lstrip('/')

        target_url = f'{self.base_url}/{path_segment}'

        try:
            response = await self.httpx_client.get(
                target_url,
                **(http_kwargs or {}),
            )
            response.raise_for_status()
            agent_card_data = response.json()
            logger.info(
                'Successfully fetched agent card data from %s',
                target_url,
            )
            agent_card = AgentCard.model_validate(agent_card_data)
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(
                e.response.status_code,
                f'Failed to fetch agent card from {target_url}: {e}',
            ) from e
        except json.JSONDecodeError as e:
            raise A2AClientJSONError(
                f'Failed to parse JSON for agent card from {target_url}: {e}'
            ) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(
                503,
                f'Network communication error fetching agent card from {target_url}: {e}',
            ) from e
        except ValidationError as e:  # Pydantic validation error
            raise A2AClientJSONError(
                f'Failed to validate agent card structure from {target_url}: {e.json()}'
            ) from e

        return agent_card
