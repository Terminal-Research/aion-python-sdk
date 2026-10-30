"""Chat command backed by the standalone Ink UI."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

import asyncclick as click

from aion.cli.services.chat import (
    BinaryResolutionError,
    ChatLaunchOptions,
    ChatRunLaunchOptions,
    launch_chat,
    launch_chat_run,
)
from aion.cli.utils.header_parser import parse_headers


def _normalize_environment_args(args: list[str], *, group: bool = False) -> list[str]:
    """Expand ``--env development [origin]`` before Click's fixed-arity parser."""
    normalized: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--" or (group and value == "run"):
            normalized.extend(args[index:])
            break
        if value.startswith("--env="):
            normalized.extend(["--env", value.partition("=")[2]])
            index += 1
        elif value == "--env" and index + 1 < len(args):
            normalized.extend(args[index:index + 2])
            index += 2
        else:
            normalized.append(value)
            index += 1
            # Other options own their next token, even when that token is --env.
            if value in {"--url", "--host", "-u", "--agent-id", "--agent", "--token",
                         "--header", "--push-receiver", "--request-mode", "--response-mode",
                         "--control-plane-origin", "--workspace"} and index < len(args):
                normalized.append(args[index])
                index += 1
            continue
        if index < len(args) and re.match(r"^[a-z][a-z0-9+.-]*://", args[index], re.I):
            normalized.extend(["--control-plane-origin", args[index]])
            index += 1
    return normalized


class _ChatGroup(click.Group):
    async def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        return await super().parse_args(ctx, _normalize_environment_args(args, group=True))


class _ChatRunCommand(click.Command):
    async def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        return await super().parse_args(ctx, _normalize_environment_args(args))


def _validate_environment_origin(environment: str | None, origin: str | None) -> None:
    if origin is None:
        return
    try:
        url = urlsplit(origin)
        valid = (environment == "development" and url.scheme in {"http", "https"}
                 and bool(url.hostname) and not url.username and not url.password
                 and url.path in {"", "/"} and not url.query and not url.fragment
                 and "?" not in origin and "#" not in origin)
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise click.UsageError("Custom API origins require --env development and an HTTP(S) origin without credentials, path, query, or fragment.")


@click.group(name="chat", invoke_without_command=True, cls=_ChatGroup)
@click.option("--env", "environment_id", type=click.Choice(["production", "staging", "development"]),
              help="Save the selected environment. Use --env development [origin] for a custom HTTP(S) API origin, e.g. http://localhost:8081. The agent endpoint set with --url is separate.")
@click.option("--control-plane-origin", default=None, hidden=True)
@click.option("--workspace", default=None, help="Workspace ID or exact name whose registry catalog to search. In run mode this overrides only this request.")
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
    environment_id: Optional[str],
    control_plane_origin: Optional[str],
    workspace: Optional[str],
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

    if workspace is not None and (not workspace.strip() or endpoint is not None):
        raise click.UsageError("--workspace requires a name or ID and cannot be combined with --url.")
    _validate_environment_origin(environment_id, control_plane_origin)
    options = ChatLaunchOptions(
        environment_id=environment_id,
        control_plane_origin=control_plane_origin,
        workspace=workspace,
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


@chat.command(name="run", cls=_ChatRunCommand, context_settings={"max_content_width": 100})
@click.option("--env", "environment_id", type=click.Choice(["production", "staging", "development"]),
              help="Save the selected environment. Use --env development [origin] for a custom HTTP(S) API origin, e.g. http://localhost:8081. The agent endpoint set with --url is separate.")
@click.option("--control-plane-origin", default=None, hidden=True)
@click.option("--workspace", default=None, help="Workspace ID or exact name whose registry catalog to search. In run mode this overrides only this request.")
@click.option(
    "--new-session",
    is_flag=True,
    help="Replace the guest identity for --url; previous conversations become inaccessible.",
)
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
@click.pass_context
def run(
    ctx: click.Context,
    new_session: bool,
    environment_id: Optional[str],
    control_plane_origin: Optional[str],
    workspace: Optional[str],
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
      --workspace selects the workspace catalog by ID or exact name.
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
    if workspace is None and ctx.parent is not None:
        workspace = ctx.parent.params.get("workspace")
    if environment_id is None and ctx.parent is not None:
        environment_id = ctx.parent.params.get("environment_id")
        control_plane_origin = control_plane_origin or ctx.parent.params.get("control_plane_origin")
    if workspace is not None and (not workspace.strip() or endpoint is not None):
        raise click.UsageError("--workspace requires a name or ID and cannot be combined with --url.")
    _validate_environment_origin(environment_id, control_plane_origin)
    options = ChatRunLaunchOptions(
        environment_id=environment_id,
        control_plane_origin=control_plane_origin,
        workspace=workspace,
        new_session=new_session,
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
