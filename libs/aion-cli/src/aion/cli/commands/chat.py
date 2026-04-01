"""Chat session command."""
from typing import Optional

import asyncclick as click

from aion.cli.handlers import start_chat
from aion.cli.utils.header_parser import parse_headers


@click.command(name="chat")
@click.option('--host', default='http://localhost:10000', help='Host URL')
@click.option('--session', default=0, help='Session ID (0 for random)')
@click.option('--history/--no-history', default=False, help='Show task history')
@click.option(
    '--push-notifications/--no-push-notifications',
    default=False,
    help='Enable push notifications'
)
@click.option(
    '--push-receiver',
    default='http://localhost:5000',
    help='Push notification receiver URL'
)
@click.option('--header', multiple=True, help='Custom headers (format: key=value)')
@click.option(
    '--extensions',
    help='Comma-separated list of extension URIs to enable',
)
@click.option(
    '--agent-id',
    default=None,
    help='Graph ID to use via proxy server',
)
@click.option(
    '--no-stream',
    is_flag=True,
    default=False,
    help='Disable streaming, use message/send even if agent supports streaming',
)
async def chat(
        host: str,
        session: int,
        history: bool,
        push_notifications: bool,
        push_receiver: str,
        header: tuple,
        extensions: Optional[str],
        agent_id: Optional[str],
        no_stream: bool,
):
    """Start an interactive chat session with A2A agent"""
    custom_headers = parse_headers(header)

    try:
        await start_chat(
            host=host,
            session_id=session,
            show_history=history,
            use_push_notifications=push_notifications,
            push_notification_receiver=push_receiver,
            enabled_extensions=extensions,
            custom_headers=custom_headers,
            agent_id=agent_id,
            no_stream=no_stream,
        )
    except KeyboardInterrupt:
        click.echo("\nChat session interrupted by user")
    except Exception as e:
        click.echo(f"Error during chat session: {e}")
