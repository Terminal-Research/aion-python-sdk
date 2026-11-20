"""Interrupt-driven commands: pause for user input, then resume."""

from __future__ import annotations

import inspect
from typing import Any

from langgraph.types import interrupt

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import (
    ASK_SCHEMA,
    ASK_SCHEMA_QUESTION,
    FAIL_MESSAGE,
    ask_answer_text,
    ask_question,
    ask_twice_answer_text,
    ask_twice_questions,
    ask_value_question,
    ask_value_text,
)

__all__ = ["ask", "ask_twice", "ask_fail", "ask_schema", "ask_value"]


def _resume_text(value: Any) -> str:
    """The user's text answer, which is what ``interrupt()`` returns for a text reply.

    Read strictly, with no fallback, so a change of what the server resumes
    with fails the interrupt scenarios instead of passing unnoticed.
    """
    if not isinstance(value, str):
        raise TypeError(f"interrupt() returned {type(value).__name__}, not the text of the reply")
    return value


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


async def ask_value(invocation: Invocation) -> None:
    """Ask with options, then report the value ``interrupt()`` returned, as JSON, and the reply's files.

    The node runs again from its start when the reply resumes it, with the
    reply as the turn's message: the files are read after ``interrupt()``
    returns, so they are the reply's.
    """
    value = interrupt(ask_value_question())
    message = invocation.thread.message
    await invocation.thread.reply(ask_value_text(value, [file.name for file in message.files] if message else ()))


async def ask_schema(invocation: Invocation) -> None:
    """Ask for an answer that matches ``ASK_SCHEMA``, then report it as JSON.

    LangGraph before 1.2.12 takes no schema, and the question goes out without one.
    """
    if "response_schema" in inspect.signature(interrupt).parameters:
        value = interrupt(ASK_SCHEMA_QUESTION, response_schema=ASK_SCHEMA)
    else:
        value = interrupt(ASK_SCHEMA_QUESTION)
    await invocation.thread.reply(ask_value_text(value))


async def ask_fail(invocation: Invocation) -> None:
    """Interrupt with the ``ask`` question, then crash on the answer.

    The crash happens on the resume turn, after the task was already paused
    once and has been taken on again - the one place a failure has a history
    behind it that the closing task must keep.
    """
    interrupt(ask_question())
    raise RuntimeError(f"{FAIL_MESSAGE}: on-resume")
