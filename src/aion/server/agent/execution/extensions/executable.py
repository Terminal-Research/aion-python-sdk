"""Executable extensions: A2A extensions whose own executor runs the task in place of the agent's framework.

An executable extension is registered with one class, its
``ExtensionExecutor``, the way the LangGraph and ADK plugins bring their
``AgentFrameworkExecutor``. The server keeps both kinds in one pool
(``TaskExecutorPool``) and calls them the same way (``TaskExecutor``): a request
that activates the extension starts a task of its executor, and every later
message and cancel of that task goes to the same executor.

The executor is created once, when the server is built, with the options
``aion.yaml`` gives the extension and the server's services
(``ExtensionServices``). It checks its options and what it needs on this
deployment itself: a constructor that raises stops the server. Everything
particular to the extension - who may call it, its payload, one task per
context - is the executor's own: it refuses a request by raising
``aion.core.exceptions.ExtensionRejection`` before it yields anything, and
the server closes the task ``REJECTED`` with the refusal's code. A message
into a running task of the extension is refused unless its executor queues
one (``TaskExecutor.message_to_busy_task``).

The executor writes its events whole: their Event markup, its state object
under its URI in the task's metadata (written whole, since a2a-sdk merges
event metadata with ``Struct.MergeFrom``), the masking of secrets and any
limits of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from aion.core.exceptions import ConfigurationError
from aion.core.runtime.context.extensions import ExtensionDescriptor
from aion.server.agent.adapters import TaskExecutor

if TYPE_CHECKING:
    from .services import ExtensionServices

__all__ = [
    "ExecutableExtensionDescriptor",
    "ExtensionExecutor",
    "parse_options",
]

OptionsModel = TypeVar("OptionsModel", bound=BaseModel)


class ExtensionExecutor(TaskExecutor):
    """Runs the tasks of one executable extension: one instance serves every task, concurrently.

    Args:
        options: The options ``aion.yaml`` gives the extension
            (``enabled_extensions: [{<uri>: {...}}]``), as written; empty for
            a bare URI. The executor validates them itself
            (:func:`parse_options`).
        services: The server's services an executor reads a context with.

    Raises:
        ConfigurationError: The options are not valid, or the extension
            cannot work on this deployment; the server does not start.
    """

    message_to_busy_task = "refuse"

    def __init__(self, options: Mapping[str, Any], services: "ExtensionServices") -> None:
        self.services = services


def parse_options(model: type[OptionsModel], uri: str, given: Mapping[str, Any]) -> OptionsModel:
    """The extension's options from ``aion.yaml``, validated against ``model``.

    Raises:
        ConfigurationError: An option the model does not have, or a value it
            refuses, named with the extension's URI.
    """
    known = set(model.model_fields) | {
        field.alias for field in model.model_fields.values() if field.alias
    }
    unknown = sorted(set(given) - known)
    if unknown:
        raise ConfigurationError(f"enabled_extensions gives {uri} options it does not take: {', '.join(unknown)}")
    try:
        return model.model_validate(dict(given))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'options'}: {item['msg']}" for item in error.errors()
        )
        raise ConfigurationError(f"enabled_extensions gives {uri} options that are not valid: {problems}") from None


@dataclass(frozen=True)
class ExecutableExtensionDescriptor(ExtensionDescriptor):
    """The registration of an executable extension: its descriptor and the class of its executor.

    Attributes:
        executor: The executor class that runs the tasks of the extension.
    """

    executor: type[ExtensionExecutor] = field(kw_only=True)
