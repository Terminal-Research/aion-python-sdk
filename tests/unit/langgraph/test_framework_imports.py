"""The standard LangGraph entry points import alongside the SDK.

An ordinary agent with tools is built from ``create_agent`` or from
``ToolNode``, not from anything of ours, and nothing else in the suite
imports either: the SDK's own code does not need them. They come from
packages the SDK does not pin directly - ``langgraph-prebuilt`` arrives with
``langgraph`` - so a release of one that no longer fits the other shows up
here, at whatever versions this environment resolved, rather than in the
first project that tries to build an agent.

The imports are written as a user writes them. Importing the packages would
pass even when the names inside are the ones that fail.
"""

from __future__ import annotations


def test_the_prebuilt_tool_node_imports() -> None:
    """``langgraph.prebuilt`` loads against the installed ``langgraph``."""
    from langgraph.prebuilt import ToolNode

    assert callable(ToolNode)


def test_langchain_create_agent_imports() -> None:
    """``langchain.agents`` loads, and with it the prebuilt it builds on."""
    from langchain.agents import create_agent

    assert callable(create_agent)
