"""The frameworks the scenarios run against, and where they differ.

Adding a framework is: an agent package under ``agents/`` that answers the
whole command contract, an entry in ``FRAMEWORKS``, and a line in
``UNSUPPORTED`` for anything the framework genuinely cannot do. Tests never
branch on the framework name.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Framework",
    "FRAMEWORKS",
    "FRAMEWORKS_BY_NAME",
    "NATIVE_AGENTS",
    "NATIVE_AGENTS_BY_NAME",
    "PREBUILT_AGENTS",
    "SUBGRAPH_AGENTS",
    "PARALLEL_AGENTS",
    "FILE_INLINING_AGENTS",
    "ASYNC_FACTORY_AGENTS",
    "SEQUENCE_AGENTS",
    "TRANSFER_AGENTS",
    "NATIVE_UNSUPPORTED",
    "native_unsupported_reason",
    "UNSUPPORTED",
    "unsupported_reason",
    "DIVERGENCES",
    "divergence_reason",
    "NO_EVENT_ROUTER",
    "no_event_router_reason",
    "implemented_commands",
    "agents_root",
]


@dataclass(frozen=True)
class Framework:
    """One agent framework under test.

    Attributes:
        name: Short name, used in test ids and on the command line.
        agent_package: Import path of the agent package shipped for it.
        agent_entry: ``<file>:<factory>`` inside that package, as
            ``aion.yaml`` spells it.
        sdk_extras: SDK extras this framework's server side needs.
    """

    name: str
    agent_package: str
    agent_entry: str
    sdk_extras: tuple[str, ...]

    @property
    def package_dir(self) -> Path:
        """Directory of the agent package on disk."""
        return agents_root() / self.agent_package.rsplit(".", 1)[-1]

    @property
    def agent_path(self) -> str:
        """Absolute ``path`` for this agent in a rendered ``aion.yaml``."""
        file_name, _, factory = self.agent_entry.partition(":")
        return f"{self.package_dir / file_name}:{factory}"


def agents_root() -> Path:
    """Directory holding the agent packages."""
    return Path(__file__).resolve().parent / "agents"


FRAMEWORKS: tuple[Framework, ...] = (
    Framework(
        name="langgraph",
        agent_package="tests.scenarios.agents.langgraph_core",
        agent_entry="graph.py:create_graph",
        sdk_extras=("langgraph-server",),
    ),
    Framework(
        name="adk",
        agent_package="tests.scenarios.agents.adk_core",
        agent_entry="agent.py:create_agent",
        sdk_extras=("adk-server",),
    ),
)

FRAMEWORKS_BY_NAME: dict[str, Framework] = {framework.name: framework for framework in FRAMEWORKS}


NATIVE_AGENTS: tuple[Framework, ...] = (
    # The second agent of each framework, for tests/scenarios/native: an
    # ordinary agent on the framework's own path - a model, a tool, the
    # framework's loop and memory - with none of the Aion authoring API. It
    # answers no part of the command contract, which is why it is not in
    # FRAMEWORKS: the matrix would hold it to every command there. Same
    # names as there, so FRAMEWORK= selects it the same way.
    Framework(
        name="langgraph",
        agent_package="tests.scenarios.agents.langgraph_native",
        agent_entry="graph.py:create_graph",
        sdk_extras=("langgraph-server",),
    ),
    Framework(
        name="adk",
        agent_package="tests.scenarios.agents.adk_native",
        agent_entry="agent.py:create_agent",
        sdk_extras=("adk-server",),
    ),
)

NATIVE_AGENTS_BY_NAME: dict[str, Framework] = {agent.name: agent for agent in NATIVE_AGENTS}


PREBUILT_AGENTS: dict[str, Framework] = {
    # The framework's one-call agent factory, where it has one, served as its
    # own agent: `native/test_prebuilt_agent.py`. It arrives compiled, which
    # is a different road through the adapter than the native StateGraph.
    "langgraph": Framework(
        name="langgraph",
        agent_package="tests.scenarios.agents.langgraph_native",
        agent_entry="graph.py:create_prebuilt_agent",
        sdk_extras=("langgraph-server",),
    ),
}


SUBGRAPH_AGENTS: dict[str, Framework] = {
    # A graph whose one node is a graph of its own, served as its own agent:
    # `native/test_subgraph.py`. What runs one level down - a model call, the
    # Aion authoring calls - reaches the adapter under the subgraph's
    # namespace, which is a different road than a node of the parent graph.
    "langgraph": Framework(
        name="langgraph",
        agent_package="tests.scenarios.agents.langgraph_subgraph",
        agent_entry="graph.py:create_graph",
        sdk_extras=("langgraph-server",),
    ),
}


PARALLEL_AGENTS: dict[str, Framework] = {
    # An agent with two branches whose models stream at once, served as its
    # own agent: `native/test_parallel_streams.py`. Parallel nodes of a graph
    # on LangGraph, the sub-agents of a ParallelAgent on ADK.
    "langgraph": SUBGRAPH_AGENTS["langgraph"],
    "adk": Framework(
        name="adk",
        agent_package="tests.scenarios.agents.adk_parallel",
        agent_entry="agent.py:create_agent",
        sdk_extras=("adk-server",),
    ),
}


FILE_INLINING_AGENTS: dict[str, Framework] = {
    # The native agent with the one Aion call that sends the files of a
    # conversation to its model by their content, served as its own agent:
    # `native/test_stored_files.py`, on a deployment that stores files.
    "langgraph": Framework(
        name="langgraph",
        agent_package="tests.scenarios.agents.langgraph_native",
        agent_entry="graph.py:create_file_inlining_graph",
        sdk_extras=("langgraph-server",),
    ),
    "adk": Framework(
        name="adk",
        agent_package="tests.scenarios.agents.adk_native",
        agent_entry="agent.py:create_file_inlining_agent",
        sdk_extras=("adk-server",),
    ),
}


ASYNC_FACTORY_AGENTS: dict[str, Framework] = {
    # The native agent returned by an `async def` factory, served as its own
    # agent: `native/test_async_factory.py`. The server has to await the
    # factory before it has an agent to serve.
    "langgraph": Framework(
        name="langgraph",
        agent_package="tests.scenarios.agents.langgraph_native",
        agent_entry="graph.py:create_async_graph",
        sdk_extras=("langgraph-server",),
    ),
    "adk": Framework(
        name="adk",
        agent_package="tests.scenarios.agents.adk_native",
        agent_entry="agent.py:create_async_agent",
        sdk_extras=("adk-server",),
    ),
}


SEQUENCE_AGENTS: dict[str, Framework] = {
    # A sequence of agents whose first step asks the user, served as its own
    # agent: `native/test_sequence_pause.py`. Resuming a pause inside a
    # workflow agent is a different road than resuming a single agent.
    "adk": Framework(
        name="adk",
        agent_package="tests.scenarios.agents.adk_sequence",
        agent_entry="agent.py:create_agent",
        sdk_extras=("adk-server",),
    ),
}


TRANSFER_AGENTS: dict[str, Framework] = {
    # A router that hands the conversation to its sub-agents, served as its
    # own agent: `native/test_agent_transfer.py`. Who answers the next
    # message is ADK's Runner's to pick, which the server runs the agent
    # through.
    "adk": Framework(
        name="adk",
        agent_package="tests.scenarios.agents.adk_transfer",
        agent_entry="agent.py:create_agent",
        sdk_extras=("adk-server",),
    ),
}


NATIVE_UNSUPPORTED: dict[tuple[str, str], str] = {
    # (framework name, capability) -> why the framework has no such thing.
    #
    # UNSUPPORTED for the native scenarios, which drive a framework's own
    # features rather than commands: a scenario names the feature with
    # @pytest.mark.capability(key) and skips on the framework listed here.
    ("langgraph", "credential-request"): (
        "LangGraph has no credential request of its own; a graph would ask through interrupt()"
    ),
    ("langgraph", "tool-confirmation"): (
        "LangGraph has no tool confirmation of its own; a graph asks through interrupt() and reads the answer itself"
    ),
    ("langgraph", "artifact-service"): (
        "LangGraph has no artifact service; a graph emits artifacts through the Aion API"
    ),
    ("adk", "prebuilt-agent"): (
        "ADK's LlmAgent is already its one standard way to build an agent; the "
        "rest of the native group runs it"
    ),
    ("adk", "subgraph"): (
        "ADK has no graphs inside graphs; it composes agents as sub_agents, which is "
        "a feature of its own"
    ),
    ("langgraph", "agent-sequence"): (
        "LangGraph has no workflow agent; a graph's edges are its sequence, and an "
        "interrupt between its nodes is `core/test_interrupts`"
    ),
    ("langgraph", "agent-transfer"): (
        "LangGraph has no transfer between agents of its own; a graph routes with its "
        "edges and keeps who is active in its state"
    ),
    ("langgraph", "error-response"): (
        "a LangChain chat model has no error response; it reports a failed call by "
        "raising, which `fail-after` drives"
    ),
    ("langgraph", "session-state"): (
        "LangGraph has no output_key or tool-written session state; its state is the "
        "graph's own channels"
    ),
}


def native_unsupported_reason(framework: str, capability: str) -> str | None:
    """Why this framework's native agent cannot do that, or None."""
    return NATIVE_UNSUPPORTED.get((framework, capability))


UNSUPPORTED: dict[tuple[str, str], str] = {
    # (framework name, command key) -> why this pair is skipped.
}


def unsupported_reason(framework: str, command_key: str) -> str | None:
    """Why this framework does not implement this command, or None."""
    return UNSUPPORTED.get((framework, command_key))


DIVERGENCES: dict[tuple[str, str], str] = {
    # (framework name, divergence key) -> what this adapter does instead.
    #
    # Different from UNSUPPORTED: there the framework cannot do the thing at
    # all and the scenario is skipped. Here both adapters do it and they
    # disagree about the result, which is a defect in one of them. The
    # scenario states the guarantee and is expected to fail on the listed
    # adapter, strictly: the day the adapter starts holding it, this entry has
    # to go or the run fails.
    #
}


def divergence_reason(framework: str, divergence_key: str) -> str | None:
    """What this framework does instead of holding that guarantee, or None."""
    return DIVERGENCES.get((framework, divergence_key))


NO_EVENT_ROUTER: dict[str, str] = {
    # framework name -> why this adapter reports no router handler.
    #
    # The event itself reaches the agent on every framework, and that is the
    # guarantee the scenarios state for all of them. Which handler it reaches
    # is a property of one adapter's authoring surface, so a scenario about
    # the handler name skips here, with the reason, instead of asking which
    # framework it is running on.
    "adk": (
        "the ADK adapter has no event router; every turn reaches the agent the same "
        "way, whatever the event is"
    ),
}


def no_event_router_reason(framework: str) -> str | None:
    """Why this framework names no router handler, or None when it names one."""
    return NO_EVENT_ROUTER.get(framework)


def implemented_commands(framework: Framework) -> frozenset[str]:
    """Command keys this framework's agent has a behaviour for.

    Read from ``<agent_package>.behaviors.BEHAVIORS``, the table the agent
    dispatches on, so the answer is the one a running agent would give. The
    import is deferred: nothing here needs the framework installed until it
    is asked about.
    """
    module = importlib.import_module(f"{framework.agent_package}.behaviors")
    return frozenset(module.BEHAVIORS)
