# AION Agent API (A2A)

Implementation of an A2A protocol server that wraps a LangGraph project.

This package exposes a small `A2AServer` utility built on top of the
Google `a2a-sdk` and Starlette.

Graphs are registered based on an ``aion.yaml`` file located in your project
root. See ``aion.yaml.example`` for the expected format.
