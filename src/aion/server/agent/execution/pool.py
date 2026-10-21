"""The executors a server runs tasks with, and which one a request or a task belongs to.

``TaskExecutorPool`` holds the agent's framework executor, which serves every task
by default, and the executor of each executable extension the agent enables,
by URI. It is a lookup and nothing more: the request executor drives every
turn the same way, whichever executor it took from here.

A new task goes to the executor of the executable extension its request
activates (``route``); the request handler keeps that choice on the call
context (``EXTENSION_ROUTE_STATE_KEY``) and the request executor records it in
the task's metadata (``aion:routedExtension``). A message into a task, and a
cancel, go to the executor the task records (``get``), on any server of the
agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from a2a.utils.errors import InvalidParamsError

from aion.core.a2a import codes
from aion.server.agent.adapters import AgentFrameworkExecutor, TaskExecutor

from .extensions import ExtensionExecutor

if TYPE_CHECKING:
    from aion.core.runtime.context.extensions import AionRuntimeExtensions

__all__ = [
    "EXTENSION_ROUTE_STATE_KEY",
    "TaskExecutorPool",
    "extension_unavailable",
    "refusal_data",
    "task_busy",
]

EXTENSION_ROUTE_STATE_KEY = "aion:extensionRoute"
"""Key of the ``ServerCallContext.state`` entry that carries the URI a new task is routed to.

The request handler decides the route before the task exists, and the
request executor reads it here: the decision is made once.
"""


def refusal_data(code: str, retryable: Optional[bool] = None) -> dict[str, str]:
    """The ``data`` of a JSON-RPC error that refuses a request with ``code``.

    a2a-sdk sends ``data`` as the metadata of a ``google.rpc.ErrorInfo``,
    whose values are strings, so ``retryable`` travels as ``"true"`` or
    ``"false"``.

    Args:
        code: One of ``aion.core.a2a.codes``.
        retryable: Whether repeating the request can succeed; by default the
            fixed answer for the code.
    """
    if retryable is None:
        retryable = codes.RETRYABLE[code]
    return {"code": code, "retryable": "true" if retryable else "false"}


def extension_unavailable(uri: str) -> InvalidParamsError:
    """The refusal of a request for a task of an extension this server does not serve."""
    return InvalidParamsError(
        message=f"The extension {uri} is not available on this server.",
        data=refusal_data(codes.EXTENSION_UNAVAILABLE),
    )


def task_busy(context_id: str, task_id: str) -> InvalidParamsError:
    """The refusal of a message into the running task ``task_id``, whose executor refuses one.

    ``data`` carries the code ``task_busy`` and, under ``taskId``, the
    running task: the caller can wait for it, or cancel it and send the
    message again.
    """
    return InvalidParamsError(
        message=(
            f"Task {task_id} in context {context_id} is still running and takes no message; "
            "wait for it to end or cancel it."
        ),
        data={**refusal_data(codes.TASK_BUSY), "taskId": task_id},
    )


@dataclass(frozen=True)
class TaskExecutorPool:
    """The agent's framework executor and its executable extensions' executors.

    Attributes:
        default: The agent's framework executor: the executor of every task
            no executable extension takes.
        extensions: The executor of each executable extension the agent
            enables, by URI.
    """

    default: AgentFrameworkExecutor
    extensions: Mapping[str, ExtensionExecutor] = field(default_factory=dict)

    def route(self, extensions: "AionRuntimeExtensions") -> Optional[str]:
        """The executable extension a new task with these extensions goes to; None for the framework executor.

        The first extension of the pool the request activates.
        """
        return next((uri for uri in self.extensions if extensions.is_active(uri)), None)

    def get(self, uri: Optional[str]) -> TaskExecutor:
        """The executor of a route: the framework executor for None, else the extension's.

        Raises:
            InvalidParamsError: ``extension_unavailable`` - no executor of
                ``uri`` on this server.
        """
        if uri is None:
            return self.default
        executor = self.extensions.get(uri)
        if executor is None:
            raise extension_unavailable(uri)
        return executor
