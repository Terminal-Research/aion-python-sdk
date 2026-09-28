"""The policy that names the caller of a request that came through a distribution."""

from unittest.mock import Mock

from aion.server.identity import DistributionCaller, resolve_distribution_caller

from tests.unit.support.distribution import distribution_extension


async def test_the_distribution_names_the_caller_without_vouching_for_it() -> None:
    credentials, user = await resolve_distribution_caller(Mock(), distribution_extension("dist-1"))

    assert isinstance(user, DistributionCaller)
    assert user.display_name == user.identity == "dist-1"
    assert user.is_authenticated is False
    assert list(credentials.scopes) == []
