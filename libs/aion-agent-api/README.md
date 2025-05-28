# AION Agent API (A2A)

Implementation of an A2A protocol server that wraps a LangGraph project.

This package exposes a small `A2AServer` utility built on top of the
Google `a2a-sdk` and Starlette.

It also provides a ``logging`` module mirroring the colorful output used by
``_langgraph_cli``. Applications can simply import ``aion_agent_api.logging`` to
apply the configuration.
