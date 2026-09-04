from typing import Optional

__all__ = [
    "AionException",
    "AionAuthenticationError",
    "AionModelPrincipalError",
]


class AionException(Exception):
    """Base AION Exception"""
    pass


class AionAuthenticationError(AionException):
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
