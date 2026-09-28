"""Does the agent answer at all: the menu and a plain echo."""

from __future__ import annotations

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import echo_text, help_text

__all__ = ["help_menu", "echo"]


async def help_menu(invocation: Invocation) -> None:
    """Answer with the command menu in one message."""
    await invocation.thread.reply(help_text())


async def echo(invocation: Invocation) -> None:
    """Answer with the argument and nothing else."""
    await invocation.thread.reply(echo_text(invocation.command.argument))
