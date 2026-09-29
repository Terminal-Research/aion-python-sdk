"""The caller a verified request token names."""

from typing import Any, Mapping

from starlette.authentication import BaseUser

__all__ = [
    "AuthenticatedCaller",
]


class AuthenticatedCaller(BaseUser):
    """The caller of a request whose bearer token the server verified.

    Named by the token's ``sub``: that caller owns the request's tasks and
    framework state, whichever channel the request came through. The token's
    other claims - the platform's ``distribution_id`` or ``caller_kind``, say -
    stay available to the agent's own logic and name nobody.
    """

    def __init__(self, subject: str, issuer: str | None, claims: Mapping[str, Any]) -> None:
        self.subject = subject
        self.issuer = issuer
        self.claims = dict(claims)

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.subject

    @property
    def identity(self) -> str:
        return self.subject
