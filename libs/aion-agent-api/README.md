# AION Agent API (A2A)

Implementation of an A2A protocol server that wraps a LangGraph project.

This package exposes a small `A2AServer` utility built on top of the
Google `a2a-sdk` and Starlette.

It also provides a ``logging`` module mirroring the colorful output used by
``_langgraph_cli``. Importing ``aion_agent_api.logging`` automatically
configures the root logger with a console handler so CLI tools immediately
produce log output.

Graphs are registered based on a ``langgraph.json`` file located in your project
root. See ``langgraph.json.example`` for the expected format.
