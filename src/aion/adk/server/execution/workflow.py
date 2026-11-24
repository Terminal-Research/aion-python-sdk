"""ADK workflows: whether the installed ADK runs them, and the output a run of one ends with.

An ADK 2.x ``Workflow`` is a graph of nodes - function nodes, agents, joins -
whose results pass from node to node as ``Event.output``. A function node's
result is in ``output`` alone, with no content; an agent node's text is its
content, as any agent's. The run's result is the output of its terminal
node: the event whose ``node_info.output_for`` names the root of the run (a
path with no ``/``), which ADK sets on the output of a terminal node.

The server reads that output as the answer (``run_output``): a string is
the reply text, any other value the run's structured response, and None
adds nothing - the task's answer is then the last message the run sent, as
for any agent's turn. The outputs of the other nodes stay inside the run,
as ADK keeps them.

ADK's ``node_info.output_for`` is what the server reads the result by; an
ADK whose events do not carry it - every release before 2.0, which has no
workflows at all - does not run a workflow here (``WORKFLOWS_SUPPORTED``).
"""

from __future__ import annotations

from typing import Any

from google.adk.events import Event

try:  # ADK 2.x
    from google.adk.workflow import Workflow
except ImportError:  # ADK 1.x has no workflows
    Workflow = None

__all__ = ["NO_OUTPUT", "TESTED_ADK_VERSION", "WORKFLOWS_SUPPORTED", "Workflow", "is_workflow", "run_output"]

TESTED_ADK_VERSION = "2.12"
"""The google-adk release the server's workflow support was built and tested against."""

NO_OUTPUT: Any = object()
"""What ``run_output`` returns for an event that does not carry the run's output."""


def _carries_output_for() -> bool:
    node_info = Event.model_fields.get("node_info")
    annotation = getattr(node_info, "annotation", None)
    for candidate in (annotation, *getattr(annotation, "__args__", ())):
        if "output_for" in getattr(candidate, "model_fields", {}):
            return True
    return False


WORKFLOWS_SUPPORTED: bool = Workflow is not None and _carries_output_for()
"""Whether the installed ADK runs workflows the server can read the result of."""


def is_workflow(obj: Any) -> bool:
    """Whether ``obj`` is an ADK ``Workflow``."""
    return Workflow is not None and isinstance(obj, Workflow)


def run_output(event: Event) -> Any:
    """The output of the whole run that ``event`` carries, or ``NO_OUTPUT``.

    An event carries it when its ``node_info.output_for`` names the root of
    the run and it holds an ``output``. An agent node's text is not one:
    ADK keeps it in the event's content and clears ``output``, so it reaches
    the client as any agent's text does.
    """
    if event.partial:
        return NO_OUTPUT
    node_info = getattr(event, "node_info", None)
    output_for = getattr(node_info, "output_for", None) or ()
    if not any("/" not in path for path in output_for):
        return NO_OUTPUT
    output = getattr(event, "output", None)
    return NO_OUTPUT if output is None else output
