"""Main CLI entry point for Aion SDK"""

import asyncclick as click

from .commands.server import serve
from .commands.chat import chat


__version__ = "0.1.0"


@click.group()
@click.version_option(version=__version__, prog_name="Aion SDK")
def cli() -> None:
    """Command line interface for the Aion Python SDK."""
    pass


cli.add_command(serve)
cli.add_command(chat)

if __name__ == "__main__":
    cli()
