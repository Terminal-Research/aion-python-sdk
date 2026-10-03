"""The caller a verified request token names."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Optional

from starlette.authentication import AuthCredentials, BaseUser

from aion.core.principal import Principal

__all__ = [
    "Assurance",
    "AuthenticatedCaller",
    "CallerCredentials",
    "CredentialKind",
    "GatewayCoordinates",
    "verified_caller",
]


class CredentialKind(StrEnum):
    """What a verified token was issued for: its ``token_use``."""

    INVOCATION = "a2a_invocation"
    """Aion dispatched the request to this agent on behalf of the caller."""

    SESSION = "anonymous_session"
    """A direct client proved it holds an anonymous session Aion issued."""


class Assurance(StrEnum):
    """What Aion established about the caller before signing; never inferred from the principal type."""

    ACCOUNT = "account"
    """A verified Aion user credential and its resolved user."""

    RUNTIME = "runtime"
    """A verified Version or AgentIdentity API credential."""

    PROVIDER = "provider"
    """A sender established by authenticated provider ingestion; not an Aion login."""

    SESSION = "session"
    """Possession of a verified anonymous session credential."""

    INTERNAL = "internal"
    """An authorized internal actor or system invocation, such as Cron."""

    UNATTRIBUTED = "unattributed"
    """Retained external-anonymous attribution with no individual access scope."""


@dataclass(frozen=True)
class GatewayCoordinates:
    """Where Aion routed an invocation: the receiving agent identity, and the edge and terminal environments."""

    owner_agent_identity_id: str
    edge_agent_environment_id: str
    terminal_agent_environment_id: str


class AuthenticatedCaller(BaseUser):
    """The caller of a request whose bearer token the server verified.

    Named by the token's canonical ``sub``: that caller owns the request's
    tasks and framework state, whichever channel the request came through.
    ``credential`` and ``assurance`` say how far to trust it - a verified
    session or provider sender is not an account login - and ``gateway`` holds
    an invocation's routing coordinates; a session has none. The remaining
    claims stay in ``claims`` and name nobody.
    """

    def __init__(
            self,
            principal: Principal,
            *,
            credential: CredentialKind,
            assurance: Assurance,
            issuer: str,
            gateway: Optional[GatewayCoordinates] = None,
            claims: Mapping[str, Any],
    ) -> None:
        self.principal = principal
        self.credential = credential
        self.assurance = assurance
        self.issuer = issuer
        self.gateway = gateway
        self.claims = dict(claims)

    @property
    def subject(self) -> str:
        """The canonical subject, ``aion:v1:<PrincipalType>:<encoded-id>``."""
        return self.principal.subject

    @property
    def session_id(self) -> Optional[str]:
        """The anonymous session's UUID, for a session token; ``None`` otherwise."""
        return self.principal.id if self.credential is CredentialKind.SESSION else None

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.subject

    @property
    def identity(self) -> str:
        return self.subject


class CallerCredentials(AuthCredentials):
    """The ``authenticated`` credentials of a verified request, carrying its caller.

    a2a-sdk hands ``ServerCallContext.user`` on wrapped in its own user type,
    but passes ``request.auth`` through as ``state["auth"]`` unchanged: that
    is where request handling finds the typed caller again.
    """

    def __init__(self, caller: AuthenticatedCaller) -> None:
        super().__init__(["authenticated"])
        self.caller = caller


def verified_caller(call_context: Any) -> Optional[AuthenticatedCaller]:
    """The caller a verified Aion token named for this call; ``None`` for any other caller."""
    user = getattr(call_context, "user", None)
    if isinstance(user, AuthenticatedCaller):
        return user
    state = getattr(call_context, "state", None) or {}
    credentials = state.get("auth")
    return credentials.caller if isinstance(credentials, CallerCredentials) else None


def has_individual_access(call_context: Any) -> bool:
    """Whether the caller may reach tasks by being their initiator.

    Every caller may, except a verified invocation whose ``assurance`` is
    ``unattributed``: its subject - the common ``ExternalAnonymous`` - is
    attribution kept for audit, shared by whoever arrived anonymously, so it
    tells no initiator from another. Such a caller may start a task in its
    gateway conversation and follow it on the same request, and nothing more.
    """
    caller = verified_caller(call_context)
    return caller is None or caller.assurance is not Assurance.UNATTRIBUTED
