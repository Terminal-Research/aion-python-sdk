"""Who calls the agent server: the caller the server names for a request.

A request that came through an Aion distribution carries the distribution's
payload in ``params.metadata``. ``CallerIdentityMiddleware`` asks a
``CallerResolver`` who sent such a request and installs the answer the way
Starlette's authentication does - credentials in ``request.scope["auth"]``,
the user in ``request.scope["user"]``. a2a-sdk's
``DefaultServerCallContextBuilder`` turns them into ``ServerCallContext``,
and the agent's ``owner_resolver`` into the owner of the request's tasks and
framework state.

``resolve_distribution_caller`` is the policy the server runs: the
distribution names the caller, and nothing verifies it.
"""

from typing import Protocol

from aion.core.a2a.extensions.distribution import DistributionExtensionV1
from starlette.authentication import AuthCredentials, BaseUser
from starlette.requests import Request

__all__ = [
    "CallerResolver",
    "DistributionCaller",
    "resolve_distribution_caller",
]


class DistributionCaller(BaseUser):
    """The caller of a request that came through an Aion distribution.

    Named by the distribution's id alone, so each channel an agent is
    published on - an A2A endpoint, a Slack workspace - is a caller of its
    own, with its own tasks and framework state.

    Not authenticated: the id comes from the request itself, and nothing yet
    proves that the platform sent the request. What the SDK serves only to an
    authenticated user - Aion's ``GetContext`` and ``GetContexts``, and
    finding an interrupted task through its ``contextId`` - stays closed to
    this caller.
    """

    def __init__(self, distribution_id: str) -> None:
        self.distribution_id = distribution_id

    @property
    def is_authenticated(self) -> bool:
        return False

    @property
    def display_name(self) -> str:
        return self.distribution_id

    @property
    def identity(self) -> str:
        return self.distribution_id


class CallerResolver(Protocol):
    """Decides who sent a request that carries a distribution payload.

    Answers with the credentials and the user, in the order Starlette's
    ``AuthenticationBackend.authenticate`` returns them. The middleware
    installs both, so they always describe the same caller; the scopes of the
    credentials say how far that caller is trusted.
    """

    async def __call__(
        self, request: Request, distribution: DistributionExtensionV1
    ) -> tuple[AuthCredentials, BaseUser]: ...


async def resolve_distribution_caller(
    request: Request, distribution: DistributionExtensionV1
) -> tuple[AuthCredentials, BaseUser]:
    """The distribution names the caller; nothing vouches for it.

    The credentials carry no scopes and the user is not authenticated,
    whatever else the request carries: its metadata names the caller without
    proving that the platform sent it.

    Args:
        request: The HTTP request, for a policy that verifies it.
        distribution: The validated distribution payload of the request.

    Returns:
        Credentials without scopes and a ``DistributionCaller``.
    """
    return AuthCredentials(), DistributionCaller(distribution.distribution.id)
