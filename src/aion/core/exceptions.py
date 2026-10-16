"""The exceptions this SDK raises, and where they are defined.

Every exception the SDK raises on its own behalf descends from
:class:`AionError`, so ``except AionError`` catches anything that came out of
Aion code rather than out of a library underneath it.

Which of them live *here* is a narrower question, and the answer is
audience-based:

* **Public** — the ones an agent author or a caller of ``aion.api`` catches
  from their own code. They belong in this module, so there is one documented
  import path to name in an ``except`` clause.
* **Internal** — the ones the server, the proxy and the CLI raise at each
  other. They stay in the module that raises them, next to the code whose
  behaviour they describe, and subclass :class:`AionError` so the root still
  holds.

Two rules keep the change from breaking callers who never asked for a
hierarchy:

* An exception that used to be an ``ImportError`` or a ``RuntimeError`` keeps
  that base as its second one — ``class X(AionError, ImportError)`` — because
  ``except ImportError`` around SDK code was correct before and stays correct.
  ``NoAdapterFoundError(AdapterError, ValueError)`` in ``aion.server.agent``
  is the same move for the same reason.
* Errors that belong to the A2A protocol rather than to Aion — ``A2AError``
  subclasses such as ``AuthenticationRequired`` — are outside this hierarchy. They
  are a2a-sdk's vocabulary, and a client catching them is catching a protocol
  error, not an SDK one.

An error that fails an agent's task can say why to whoever reads the task:
:attr:`AionError.public_reason` is that sentence, written so a caller may read
it, and ``None`` when the error's own text is internal detail.
:class:`TaskFailedError` is the one an agent raises to fail its task with a
reason of its own.

``aion`` is a namespace package with no ``__init__.py`` (a second distribution
shares the namespace — see ``scripts/packaging/check.py``), so a top-level
``aion.exceptions`` facade is not available. ``aion.core`` is the layer
everything else already imports, which makes this the one place a common root
can live.
"""

from pathlib import Path
from typing import Optional

__all__ = [
    "AionError",
    "TaskFailedError",
    "MissingOptionalDependency",
    "ConfigurationError",
    "AionAuthenticationError",
    "AionFileValidationError",
    "AionModelPrincipalError",
    "InvalidPrincipalError",
    "AionDaemonIdentityRequired",
    "ToolkitRejection",
]


class AionError(Exception):
    """Root of every exception the SDK raises on its own behalf."""

    @property
    def public_reason(self) -> Optional[str]:
        """Why this error failed a task, in words the task's caller may read.

        When an agent's turn ends on this error, the server puts the sentence
        on the task's FAILED status, where the stream, the unary answer and a
        later ``tasks/get`` all show it. ``None`` - the default - keeps the
        error's text out of the task: it names paths, hosts or identities
        the caller has no business seeing, and the task fails with a generic
        sentence instead.
        """
        return None


class TaskFailedError(AionError):
    """Raise from an agent to fail its task with a reason the caller reads.

    The reason becomes the message of the task's FAILED status as it is
    written, so it is for the agent's caller: what went wrong in their terms,
    and what to do about it, without internal detail. The turn ends there;
    what the agent sent before raising has already been delivered. Raised
    from a framework callback or tool, it may reach the server wrapped by the
    framework - the server looks for it along the chain of causes.

    Example:
        from aion.core.exceptions import TaskFailedError

        async def forecast(state):
            if not weather_service.available():
                raise TaskFailedError("The weather service is unavailable; try again in a few minutes.")

    Args:
        reason: The sentence the task's caller reads.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @property
    def public_reason(self) -> str:
        """The reason the agent gave, as it gave it."""
        return self.reason


class ToolkitRejection(AionError):
    """Raise from a toolkit's ``preflight`` to refuse a request before its task exists.

    The caller gets a JSON-RPC ``InvalidParams`` error whose ``data`` carries
    ``code`` and ``retryable`` (as strings: the A2A error details carry
    string values), and no task is created.

    Args:
        code: Stable machine-readable name of the refusal, such as
            ``invalid_payload``; never empty.
        message: Why, in words the caller may read.
        retryable: Whether repeating the same request can succeed.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        if not code:
            raise ValueError("a toolkit rejection needs a code")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    @property
    def public_reason(self) -> str:
        """The refusal's message, written for the caller."""
        return self.message


class InvalidPrincipalError(AionError, ValueError):
    """A caller ID is not canonical; successful parsing would not authenticate it."""


class MissingOptionalDependency(AionError, ImportError):
    """An optional extra of this distribution is not installed.

    Also an ``ImportError``: it is raised in place of the ``ModuleNotFoundError``
    that would otherwise escape an optional import, and callers who guard SDK
    imports with ``except ImportError`` keep working. The guards in
    ``aion.server`` and ``aion.proxy`` catch this class specifically, to tell an
    uninstalled extra from a broken installation.

    See ``aion.core.utils.optional_deps`` for that distinction and for the
    factories that build these errors with an install command in the message.
    """


class ConfigurationError(AionError):
    """Custom exception for configuration errors with readable messages."""

    def __init__(self, message: str, details: Optional[str] = None, file_path: Optional[Path] = None):
        self.message = message
        self.details = details
        self.file_path = file_path

        full_message = message
        if file_path:
            full_message = f"Configuration error in {file_path}: {message}"
        if details:
            full_message += f"\nDetails: {details}"

        super().__init__(full_message)


class AionAuthenticationError(AionError):
    """Authentication related errors.

    Args:
        message (str): Human readable description of the failure.
        status_code (Optional[int]): HTTP status returned by the auth endpoint
            when the error originated from a response, ``None`` otherwise.
            Callers use this to tell rejected credentials (401) apart from
            failures that are worth retrying.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def public_reason(self) -> str:
        """That the agent could not authenticate, without the endpoint's answer."""
        return "The agent could not authenticate with the Aion platform."


class AionDaemonIdentityRequired(ConfigurationError):
    """A callback needs an explicitly assigned daemon, not different credentials.

    Repair the deployment or agent environment's Identity tab. Retrying or
    selecting the reported caller as executor cannot repair this configuration.
    The message names the resource Aion reports as missing the daemon, such as
    the deployment and its id, when the response carries it.

    Args:
        resource_type: Kind of resource that needs a daemon, as Aion reports it.
        resource_id: Id of that resource.
    """

    code = "daemon_identity_required"
    retryable = False

    @property
    def public_reason(self) -> str:
        """What is missing and where it is fixed, naming the kind of resource but not its id."""
        resource = self.resource_type or "deployment"
        return (
            f"The agent has no daemon identity assigned to its {resource}; "
            "an administrator assigns one in its Identity tab."
        )

    def __init__(self, resource_type: str | None = None, resource_id: str | None = None):
        self.resource_type = resource_type
        self.resource_id = resource_id
        resource = " ".join(part for part in (resource_type, resource_id) if part)
        target = f" to {resource}" if resource else ""
        super().__init__(
            f"Assign a daemon identity{target} in the deployment or agent environment's "
            "Identity tab before making this callback (daemon_identity_required)."
        )


class AionFileValidationError(AionError, ValueError):
    """Invalid Files API arguments rejected before sending a request.

    Also a ValueError so callers that already catch invalid argument errors
    continue to work while SDK-wide handlers can catch AionError.
    """


class AionModelPrincipalError(AionAuthenticationError):
    """A model call has no principal the model service will run work for.

    Used when normalizing ordinary GraphQL model principal arguments. SDK
    callbacks instead use typed request-local attribution; a missing assigned
    executor is reported by AionDaemonIdentityRequired after backend resolution.

    Args:
        message: What is missing and where it is fixed.
        selector: Principal selector the runtime context resolved to, when it
            resolved to one the model service does not accept. ``None`` when
            no selector was resolved at all.
    """

    def __init__(self, message: str, selector: Optional[str] = None) -> None:
        super().__init__(message)
        self.selector = selector

    @property
    def public_reason(self) -> str:
        """That the model call has no principal, without the selector it resolved to."""
        return "The agent's model call has no principal the Aion model service will run work for."
