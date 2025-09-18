"""Chat session command"""
from typing import Optional
import asyncclick as click

from aion.cli.handlers import start_chat


@click.command(name="chat")
@click.option('--host', default='http://localhost:10000', help='Host URL')
@click.option(
    '--bearer-token',
    help='Bearer token for authentication',
    envvar='AION_CLI_BEARER_TOKEN',
)
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
    '--graph_id',
    default=None,
    help='Graph ID to use via proxy server',
)
async def chat(
        host: str,
        bearer_token: Optional[str],
        session: int,
        history: bool,
        push_notifications: bool,
        push_receiver: str,
        header: tuple,
        extensions: Optional[str],
        graph_id: Optional[str],
):
    """Start an interactive chat session with A2A agent"""
    custom_headers = _parse_headers(header)

    try:
        await start_chat(
            host=host,
            bearer_token=bearer_token,
            session_id=session,
            show_history=history,
            use_push_notifications=push_notifications,
            push_notification_receiver=push_receiver,
            enabled_extensions=extensions,
            custom_headers=custom_headers,
            graph_id=graph_id,
        )
    except KeyboardInterrupt:
        click.echo("\nChat session interrupted by user")
    except Exception as e:
        click.echo(f"Error during chat session: {e}")


def _parse_headers(header: tuple) -> dict:
    """Parse custom headers from command line arguments.

    Args:
        header: Tuple of header strings in format 'key=value'

    Returns:
        Dictionary of parsed headers
    """
    custom_headers = {}
    for h in header:
        if '=' in h:
            key, value = h.split('=', 1)
            custom_headers[key] = value
        else:
            click.echo(f"Warning: Invalid header format '{h}', expected 'key=value'")
    return custom_headers
