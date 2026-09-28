"""The one card a scenario asks for."""

from __future__ import annotations

from aion.core.agent.invocation.card import Card

from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.commands import card_jsx, card_text

__all__ = ["card"]


async def card(invocation: Invocation) -> None:
    """Post the scenario card, then say it is out.

    The document comes from commands.card_jsx() and is handed over as a
    value object rather than built from the component classes: what the
    client has to receive is that exact string, so the agent must not be the
    one deciding how it renders.
    """
    await invocation.thread.post(Card(jsx=card_jsx()))
    await invocation.thread.reply(card_text())
