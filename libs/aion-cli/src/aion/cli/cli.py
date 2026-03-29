"""Main CLI entry point for Aion SDK"""

import asyncclick as click

from . import commands


__version__ = "0.1.0"


@click.group()
@click.version_option(version=__version__, prog_name="Aion SDK")
def cli() -> None:
    """Command line interface for the Aion Python SDK."""
    pass


cli.add_command(commands.serve)
cli.add_command(commands.chat)

if __name__ == "__main__":
    cli()
