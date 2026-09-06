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
  subclasses such as ``TaskOwnershipBusy`` — are outside this hierarchy. They
  are a2a-sdk's vocabulary, and a client catching them is catching a protocol
  error, not an SDK one.

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
    "MissingOptionalDependency",
    "ConfigurationError",
    "AionAuthenticationError",
    "AionFileValidationError",
    "AionModelPrincipalError",
]


class AionError(Exception):
    """Root of every exception the SDK raises on its own behalf."""


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


class AionFileValidationError(AionError, ValueError):
    """Invalid Files API arguments rejected before sending a request.

    Also a ValueError so callers that already catch invalid argument errors
    continue to work while SDK-wide handlers can catch AionError.
    """


class AionModelPrincipalError(AionAuthenticationError):
    """A model call has no principal the model service will run work for.

    Deployment credentials authenticate the agent *version*. The model service
    does not execute work for a version: it needs the runtime principal — the
    environment's Daemon Identity — which travels in the
    ``Aion-Principal-Selector`` header and is resolved from the invocation's
    environment.

    The refusal happens server-side either way, but it arrives as a statement
    about the protocol rather than about the deployment. Raising here instead
    keeps the explanation next to the cause and spares a round trip that was
    going to fail.

    Args:
        message: What is missing and where it is fixed.
        selector: Principal selector the runtime context resolved to, when it
            resolved to one the model service does not accept. ``None`` when
            no selector was resolved at all.
    """

    def __init__(self, message: str, selector: Optional[str] = None) -> None:
        super().__init__(message)
        self.selector = selector
