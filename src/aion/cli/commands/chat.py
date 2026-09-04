"""Chat command backed by the standalone Ink UI."""

from __future__ import annotations

from typing import Optional

import asyncclick as click

from aion.cli.services.chat import (
    BinaryResolutionError,
    ChatLaunchOptions,
    ChatRunLaunchOptions,
    launch_chat,
    launch_chat_run,
)
from aion.cli.utils.header_parser import parse_headers


@click.group(name="chat", invoke_without_command=True)
@click.option(
    "--url",
    "--host",
    "-u",
    "endpoint",
    default=None,
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
@click.pass_context
def chat(
    ctx: click.Context,
    endpoint: Optional[str],
    agent_id: Optional[str],
    token: Optional[str],
    header: tuple[str, ...],
    push_notifications: bool,
    push_receiver: str,
) -> None:
    """Launch the interactive chat UI."""
    if ctx.invoked_subcommand is not None:
        return

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


@chat.command(name="run", context_settings={"max_content_width": 100})
@click.option(
    "--url",
    "--host",
    "-u",
    "endpoint",
    default=None,
    help="Agent or proxy URL to connect to.",
)
@click.option(
    "--agent-id",
    default=None,
    help="Agent identifier to use via proxy-aware routing.",
)
@click.option(
    "--agent",
    default=None,
    help="Discovered agent selector, such as an @ handle.",
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
    help="Enable push notification configuration.",
)
@click.option(
    "--push-receiver",
    default="http://localhost:5000",
    help="Push notification receiver URL.",
)
@click.option(
    "--request-mode",
    type=click.Choice(["send-message", "streaming-message"]),
    default="send-message",
    show_default=True,
    help="A2A request mode.",
)
@click.option(
    "--response-mode",
    type=click.Choice(["message", "a2a"]),
    default="message",
    show_default=True,
    help="Output rendering mode.",
)
@click.argument("message", nargs=-1, required=False)
def run(
    endpoint: Optional[str],
    agent_id: Optional[str],
    agent: Optional[str],
    token: Optional[str],
    header: tuple[str, ...],
    push_notifications: bool,
    push_receiver: str,
    request_mode: str,
    response_mode: str,
    message: tuple[str, ...],
) -> None:
    """Send one non-interactive message through the chat UI.

    The run command uses the currently selected Aion environment for account-backed
    access and registry discovery. It does not open the terminal UI.

    MESSAGE can be passed as positional text. Use "-" to read MESSAGE from stdin,
    or pipe stdin without passing a positional message.

    \b
    Agent selection:
      --agent selects a discovered agent by @ handle, display id, identity id, or agent key.
      --agent-id is for proxy-aware routing when --url points at an explicit A2A endpoint.

    \b
    Output:
      message mode writes rendered agent output to stdout and diagnostics to stderr.
      a2a mode writes raw A2A JSON to stdout. Streaming a2a mode writes JSONL events.
      streaming-message falls back to send-message if the agent does not support streaming.

    \b
    Examples:
      aion chat run --agent @team-agent "Summarize the latest status"
      cat prompt.txt | aion chat run --agent @team-agent -
      aion chat run --url http://localhost:8000 --agent-id demo-agent "Hello"
      aion chat run --agent @team-agent --request-mode streaming-message "Hello"
      aion chat run --agent @team-agent --response-mode a2a "Hello"
    """
    options = ChatRunLaunchOptions(
        endpoint=endpoint,
        agent_id=agent_id,
        agent=agent,
        token=token,
        headers=parse_headers(header),
        push_notifications=push_notifications,
        push_receiver=push_receiver,
        request_mode=request_mode,
        response_mode=response_mode,
        message=" ".join(message) if message else None,
    )

    try:
        exit_code = launch_chat_run(options)
    except BinaryResolutionError as exc:
        raise click.ClickException(str(exc)) from exc

    raise click.exceptions.Exit(exit_code)
