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


UNSUPPORTED: dict[tuple[str, str], str] = {
    # (framework name, command key) -> why this pair is skipped.
    ("adk", "artifacts"): (
        "the ADK artifact service stores one part per artifact, so the SDK refuses "
        "the two-part data artifact this command emits (aion.adk emit_artifact)"
    ),
    ("adk", "ask"): "ADK has no interrupt/pause primitive; resume is a plain message send",
    ("adk", "ask-twice"): "ADK has no interrupt/pause primitive; resume is a plain message send",
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
