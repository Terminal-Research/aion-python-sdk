"""Interrupt-driven commands: pause for user input, then resume.

A custom ADK agent pauses the way ADK's own ``request_input`` tool does: it
yields an event that calls ``adk_request_input`` with the question, marks the
call long-running, and returns. The user's reply resumes the invocation with
the function response to that call as the turn's user content, which is
where ``resume`` reads the answer - and, from the call it answers, which
command asked and what it had heard so far.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from google.adk.events import Event
from google.genai import types

from tests.scenarios.agents.adk_core.invocation import Invocation
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

__all__ = ["ask", "ask_fail", "ask_schema", "ask_twice", "ask_value", "resume"]

AGENT_NAME = "scenario_agent"
REQUEST_INPUT = "adk_request_input"


def _request_input(
    question: str,
    *,
    command: str,
    heard: Optional[str] = None,
    options: Optional[list[str]] = None,
    response_schema: Optional[dict[str, Any]] = None,
) -> Event:
    """The event that asks the user and pauses the run."""
    call_id = f"adk-{uuid.uuid4()}"
    args: dict[str, Any] = {"message": question, "command": command}
    if options is not None:
        args["options"] = options
    if response_schema is not None:
        args["response_schema"] = response_schema
    if heard is not None:
        args["heard"] = heard
    return Event(
        author=AGENT_NAME,
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=REQUEST_INPUT, args=args))],
        ),
        long_running_tool_ids={call_id},
    )


async def ask(invocation: Invocation) -> Event:
    """Ask one question; ``resume`` quotes the answer back."""
    return _request_input(ask_question(), command="ask")


async def ask_twice(invocation: Invocation) -> Event:
    """Ask the first of two questions; ``resume`` asks the second, then replies with both answers."""
    first, _ = ask_twice_questions()
    return _request_input(first, command="ask-twice")


async def ask_value(invocation: Invocation) -> Event:
    """Ask with options; ``resume`` reports the function response the reply became, as JSON."""
    question = ask_value_question()
    return _request_input(question["prompt"], command="ask-value", options=question["options"])


async def ask_schema(invocation: Invocation) -> Event:
    """Ask for an answer that matches ``ASK_SCHEMA``, as ``request_input`` does; ``resume`` reports it as JSON."""
    return _request_input(ASK_SCHEMA_QUESTION, command="ask-schema", response_schema=ASK_SCHEMA)


async def ask_fail(invocation: Invocation) -> Event:
    """Ask the ``ask`` question; ``resume`` crashes on the answer."""
    return _request_input(ask_question(), command="ask-fail")


async def resume(invocation: Invocation, call: types.FunctionCall, response: dict[str, Any]) -> Optional[Event]:
    """Go on from the question ``call`` asked, now that the reply came back as ``response``."""
    args = dict(call.args or {})
    command = args.get("command")
    if command == "ask-schema":
        await invocation.thread.reply(ask_value_text(response))
        return None
    if command == "ask-value":
        message = invocation.thread.message
        await invocation.thread.reply(ask_value_text(response, [file.name for file in message.files] if message else ()))
        return None
    answer = str(response.get("result", ""))
    if command == "ask":
        await invocation.thread.reply(ask_answer_text(answer))
        return None
    if command == "ask-twice":
        if "heard" not in args:
            _, second = ask_twice_questions()
            return _request_input(second, command="ask-twice", heard=answer)
        await invocation.thread.reply(ask_twice_answer_text(args["heard"], answer))
        return None
    if command == "ask-fail":
        raise RuntimeError(f"{FAIL_MESSAGE}: on-resume")
    raise RuntimeError(f"no command asked call {call.id}")
