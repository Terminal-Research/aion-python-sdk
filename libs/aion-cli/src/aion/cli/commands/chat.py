"""Chat command backed by the standalone Ink UI."""

from __future__ import annotations

from typing import Optional

import asyncclick as click

from aion.cli.services.chat import (
    BinaryResolutionError,
    ChatLaunchOptions,
    launch_chat,
)
from aion.cli.utils.header_parser import parse_headers


@click.command(name="chat")
@click.option(
    "--url",
    "--host",
    "-u",
    "endpoint",
    default="http://localhost:8000",
    show_default=True,
    help="Agent or proxy URL to connect to.",
)
@click.option(
    "--agent-id",
    default=None,
    help="Agent identifier to use via proxy-aware routing.",
)
@click.option(
    "--token",
    default=None,
    help="Bearer token for authenticated A2A endpoints.",
)
@click.option(
    "--header",
    multiple=True,
    help="Custom HTTP header in key=value format. Can be repeated.",
)
@click.option(
    "--push-notifications/--no-push-notifications",
    default=False,
    help="Enable the local push notification receiver.",
)
@click.option(
    "--push-receiver",
    default="http://localhost:5000",
    help="Push notification receiver URL.",
)
def chat(
    endpoint: str,
    agent_id: Optional[str],
    token: Optional[str],
    header: tuple[str, ...],
    push_notifications: bool,
    push_receiver: str,
) -> None:
    """Launch the interactive chat UI."""
    options = ChatLaunchOptions(
        endpoint=endpoint,
        agent_id=agent_id,
        token=token,
        headers=parse_headers(header),
        push_notifications=push_notifications,
        push_receiver=push_receiver,
    )

    try:
        exit_code = launch_chat(options)
    except BinaryResolutionError as exc:
        raise click.ClickException(str(exc)) from exc

    raise click.exceptions.Exit(exit_code)
