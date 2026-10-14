"""Main CLI entry point for Aion SDK"""

from importlib.metadata import PackageNotFoundError, version

import asyncclick as click

from aion.core.utils.optional_deps import DISTRIBUTION

from . import commands
from .utils.project_env import load_project_env


try:
    __version__ = version(DISTRIBUTION)
except PackageNotFoundError:
    # Running from a source tree that was never installed - a checkout on
    # sys.path, or a build environment. Report something ordered below every
    # real release rather than a number that would look like one.
    __version__ = "0.0.0+dev"


@click.group()
@click.version_option(version=__version__, prog_name="Aion SDK")
def cli() -> None:
    """Command line interface for the Aion Python SDK."""
    # Before any command runs, and so before anything reads a setting: the
    # settings of the SDK read the process environment only.
    load_project_env()


cli.add_command(commands.serve)
cli.add_command(commands.chat)
cli.add_command(commands.logs)
cli.add_command(commands.db)

if __name__ == "__main__":
    cli()
