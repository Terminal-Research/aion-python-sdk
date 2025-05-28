"""A minimal A2A server wrapping a LangGraph project."""

from __future__ import annotations

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import AgentCard
from starlette.applications import Starlette


class A2AServer:
    """Simple wrapper exposing a LangGraph project via the A2A protocol."""

    def __init__(self, agent_card: AgentCard, handler: RequestHandler) -> None:
        """Create a server.

        Args:
            agent_card: Metadata describing the agent.
            handler: Implementation of the A2A request handler.
        """
        self._agent_card = agent_card
        self._handler = handler
        self._app: Starlette | None = None

    def build_app(self) -> Starlette:
        """Build the underlying Starlette application."""
        application = A2AStarletteApplication(
            agent_card=self._agent_card, http_handler=self._handler
        )
        self._app = application.build()
        return self._app

    def run(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Run the server using ``uvicorn``."""
        app = self._app or self.build_app()
        uvicorn.run(app, host=host, port=port)
