"""Commands that inspect the runtime context: extensions, config, identity."""

from __future__ import annotations

from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.commands import ConfigResponse, EventResponse, ExtResponse, WhoamiResponse

__all__ = ["config", "ext", "whoami", "event"]


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


async def whoami(invocation: Invocation) -> None:
    """Answer with the identity the daemon extension named for this turn."""
    daemon = invocation.context.get_daemon()
    if daemon is None:
        await invocation.thread.reply(WhoamiResponse().model_dump_json())
        return
    requester = daemon.requester_identity
    await invocation.thread.reply(
        WhoamiResponse(
            daemon_identity_id=daemon.daemon_identity.id,
            requester_identity_id=requester.id if requester else None,
            environment=daemon.environment.id,
            behavior=daemon.behavior.id,
        ).model_dump_json()
    )


async def event(invocation: Invocation) -> None:
    """Answer with the event this turn carried.

    No handler name: the ADK adapter has no event router, so every turn
    reaches the agent the same way, whatever the event is.
    """
    inbound = invocation.context.event
    await invocation.thread.reply(
        EventResponse(
            kind=inbound.kind if inbound else None,
            event_id=inbound.id if inbound else None,
            payload=inbound.payload.model_dump(exclude_none=True)
            if inbound is not None and inbound.payload is not None
            else {},
        ).model_dump_json()
    )
