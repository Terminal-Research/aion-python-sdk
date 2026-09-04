"""Allow running the CLI as ``python -m aion.cli``."""

from aion.cli.cli import cli


if __name__ == "__main__":
    cli()
