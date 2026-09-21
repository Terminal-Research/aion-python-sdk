"""Commands that inspect the runtime context: extensions, config, identity."""

from __future__ import annotations

from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.commands import ConfigResponse, ExtResponse

__all__ = ["config", "ext"]


async def config(invocation: Invocation) -> None:
    """Report one configuration variable from the environment."""
    key = invocation.command.argument.strip()
    env = invocation.context.get_environment()
    if env is None:
        await invocation.thread.reply(
            ConfigResponse(key=key, present=False).model_dump_json()
        )
        return
    value = env.get_configuration_variable(key)
    await invocation.thread.reply(
        ConfigResponse(key=key, present=value is not None, value=value).model_dump_json()
    )


async def ext(invocation: Invocation) -> None:
    """Report active extensions and event metadata."""
    extensions = invocation.context.extensions
    active = sorted(
        uri for uri in extensions if extensions.is_active(uri)
    )
    unknown = sorted(u.uri for u in extensions.unknown)
    event = invocation.context.event
    await invocation.thread.reply(
        ExtResponse(
            active=active,
            unknown=unknown,
            event_kind=event.kind if event else None,
            event_id=event.id if event else None,
            has_distribution=invocation.context.get_distribution() is not None,
        ).model_dump_json()
    )
