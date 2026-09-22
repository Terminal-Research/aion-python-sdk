"""Interrupt-driven commands: pause for user input, then resume."""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import (
    ask_answer_text,
    ask_question,
    ask_twice_answer_text,
    ask_twice_questions,
)

__all__ = ["ask", "ask_twice"]


def _resume_text(value: Any) -> str:
    """Extract the user's text from the resume input.

    ``interrupt()`` returns the ``Command(resume=...)`` payload, which is the
    LangGraph state dict the server built from the inbound A2A message.  The
    text lives in the first HumanMessage under ``messages``.
    """
    if isinstance(value, dict):
        messages = value.get("messages", [])
        if messages:
            content = messages[0].content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("text"):
                        return block["text"]
            if isinstance(content, str):
                return content
    return str(value)


async def ask(invocation: Invocation) -> None:
    """Interrupt with one question, resume with the answer quoted back."""
    answer = interrupt(ask_question())
    await invocation.thread.reply(ask_answer_text(_resume_text(answer)))


async def ask_twice(invocation: Invocation) -> None:
    """Interrupt twice in a row, then reply with both answers."""
    q1, q2 = ask_twice_questions()
    answer_1 = interrupt(q1)
    answer_2 = interrupt(q2)
    await invocation.thread.reply(
        ask_twice_answer_text(_resume_text(answer_1), _resume_text(answer_2))
    )
