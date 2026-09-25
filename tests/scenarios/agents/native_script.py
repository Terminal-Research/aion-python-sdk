"""What the stand-in model answers, for both native agents.

The native agents are ordinary framework agents: a model, a tool, the
framework's own loop and memory. The one thing they cannot have is a real
model, so the model is this script - deterministic, offline, and the same
on both frameworks. Each framework wraps it in its own model interface
(``langchain_core``'s ``BaseChatModel``, ADK's ``BaseLlm``); everything
around the model is the framework's.

The script reads what a model reads - the conversation - and decides from the
latest user message, the way a scenario decides what to send:

    say <text>          answer with <text>, streamed a word at a time
    weather <city>      say what it is about to do, call ``get_weather``,
                        then answer with the result: two model calls
    tool-only <city>    call ``get_weather`` without a word first, then answer
    recall              answer with every earlier user message of the context
    inputs              answer with a JSON description of the user message's
                        parts, as the model received them
    fail-after <n>      stream <n> words, then fail
    slow-say <path>     stream a long answer slowly, appending each word to
                        <path> as it goes - progress a scenario can watch
    slow-tool <path>    call ``ticker``, which appends to <path> until it is
                        stopped, then answer
    confirm <action>    call ``confirm_action``, which asks the user (LangGraph
                        ``interrupt``), then answer with what they said
    report <title>      call ``save_report``, which saves an ADK artifact
    remember <note>     call ``remember``, which writes the note to ADK state
    instruction         answer with the system instruction the model received
    think <text>        think ``THOUGHT`` first, streamed as the framework's
                        reasoning, then answer with <text>
    counted <path>      say ``WEATHER_PREAMBLE``, call ``count_call``, which
                        appends a line to <path> per call, then answer - how
                        a scenario sees that the tool ran exactly once
    fail-after-tool <n> say ``WEATHER_PREAMBLE``, call ``get_weather``, then
                        stream <n> words of the answer and fail

Anything else is answered with ``UNKNOWN_REPLY``. The tools behind the last
four are the framework's own - an interrupt, an artifact service, session
state - so each agent defines the ones its framework has.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "RECALL_PREFIX",
    "NOTED",
    "REPORT_FILENAME",
    "REPORT_SAVED",
    "THOUGHT",
    "UNKNOWN_REPLY",
    "COUNTED_ANSWER",
    "append_progress",
    "count_call",
    "confirmed_answer",
    "report_text",
    "ticker",
    "WEATHER_PREAMBLE",
    "FAILURE_TEXT",
    "ModelFailure",
    "ModelTurn",
    "UserMessage",
    "get_weather",
    "model_turn",
    "tokens",
    "weather_answer",
    "weather_result",
]

WEATHER_TOOL = "get_weather"
TICKER_TOOL = "ticker"
COUNT_TOOL = "count_call"
COUNTED_ANSWER = "Counted."
CONFIRM_TOOL = "confirm_action"
REPORT_TOOL = "save_report"
REMEMBER_TOOL = "remember"
SLOW_WORDS = 600
SLOW_DELAY_SECONDS = 0.05
TICKER_SECONDS = 60
REPORT_SAVED = "Report saved."
REPORT_FILENAME = "report.txt"
THOUGHT = "The user wants a short answer."
NOTED = "Noted."
WEATHER_PREAMBLE = "Checking the weather."
RECALL_PREFIX = "You said: "
UNKNOWN_REPLY = "I do not know that one."
FAILURE_TEXT = "the stand-in model failed on purpose"


class ModelFailure(RuntimeError):
    """What ``fail-after`` raises, from inside the model call."""


def get_weather(city: str) -> str:
    """Report the weather in a city.

    Args:
        city: The city to report on.
    """
    return weather_result(city)


async def ticker(path: str) -> str:
    """Append a tick to a file every few milliseconds, for a minute.

    Args:
        path: The file to append to.
    """
    loops = int(TICKER_SECONDS / SLOW_DELAY_SECONDS)
    for _ in range(loops):
        append_progress(path, "tick\n")
        await asyncio.sleep(SLOW_DELAY_SECONDS)
    return "ticked"


def count_call(path: str) -> str:
    """Record one call of this tool.

    Args:
        path: The file each call appends a line to.
    """
    append_progress(path, "call\n")
    return "counted"


def append_progress(path: Optional[str], text: str) -> None:
    """Record progress where a scenario can see it."""
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def report_text(title: str) -> str:
    """The content of the artifact ``save_report`` saves."""
    return f"Report: {title}"


def confirmed_answer(answer: str) -> str:
    """What the model says once the user answered ``confirm_action``."""
    return f"Done: {answer}"


def weather_result(city: str) -> str:
    """What the tool returns for a city."""
    return f"sunny in {city}"


def weather_answer(city: str) -> str:
    """What the model says once it has the tool's result."""
    return f"It is {weather_result(city)}."


@dataclass(frozen=True)
class UserMessage:
    """A user message, reduced to what the script reads from it.

    Attributes:
        text: Its text parts, joined.
        parts: One description per part, in the form ``inputs`` answers
            with: ``{"type": "text", "text": ...}`` or ``{"type": "file",
            "mime_type": ..., "size": ...}`` / ``{..., "url": ...}``.
    """

    text: str
    parts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ModelTurn:
    """One model call's answer.

    Attributes:
        text: What the model says, streamed a word at a time.
        tool_call: ``(name, args)`` of a tool to call after the text, if any.
        fail_after: Raise ``ModelFailure`` after this many words, if set.
        delay: Seconds to wait before each word.
        progress_path: A file each word is appended to as it streams.
        thought: Reasoning the model streams before its text, if any.
    """

    text: str = ""
    tool_call: Optional[tuple[str, dict[str, Any]]] = None
    fail_after: Optional[int] = None
    delay: float = 0.0
    progress_path: Optional[str] = None
    thought: str = ""


def tokens(text: str) -> list[str]:
    """The pieces a text streams in: one word each, with its trailing space."""
    return re.findall(r"\S+\s*", text)


def model_turn(
    user: UserMessage,
    *,
    tool_results: list[str],
    earlier_user_texts: list[str],
    instruction: str = "",
) -> ModelTurn:
    """What the model answers.

    Args:
        user: The latest user message.
        tool_results: What tools returned since that message, in order.
        earlier_user_texts: The user's earlier messages in the context.
        instruction: The system instruction the model was given, if any.
    """
    # The command is the first text part: a data part arrives as text too,
    # and joined onto it would change the command.
    first_text = next((part["text"] for part in user.parts if part.get("type") == "text"), user.text)
    command, _, argument = first_text.strip().partition(" ")
    argument = argument.strip()

    if command == "say":
        return ModelTurn(text=argument)
    if command in ("weather", "tool-only"):
        if tool_results:
            return ModelTurn(text=weather_answer(argument))
        preamble = WEATHER_PREAMBLE if command == "weather" else ""
        return ModelTurn(text=preamble, tool_call=(WEATHER_TOOL, {"city": argument}))
    if command == "recall":
        return ModelTurn(text=RECALL_PREFIX + " | ".join(earlier_user_texts))
    if command == "inputs":
        return ModelTurn(text=json.dumps(list(user.parts), sort_keys=True))
    if command == "fail-after":
        count = int(argument or "1")
        return ModelTurn(text=" ".join(f"word{index}" for index in range(count + 5)), fail_after=count)
    if command == "slow-say":
        return ModelTurn(
            text=" ".join(f"word{index}" for index in range(SLOW_WORDS)),
            delay=SLOW_DELAY_SECONDS,
            progress_path=argument,
        )
    tools = {
        "slow-tool": (TICKER_TOOL, {"path": argument}, lambda result: result),
        "confirm": (CONFIRM_TOOL, {"action": argument}, confirmed_answer),
        "report": (REPORT_TOOL, {"title": argument}, lambda result: REPORT_SAVED),
        "remember": (REMEMBER_TOOL, {"note": argument}, lambda result: NOTED),
    }
    if command in tools:
        name, args, answer = tools[command]
        if tool_results:
            return ModelTurn(text=answer(tool_results[-1]))
        return ModelTurn(tool_call=(name, args))
    if command == "instruction":
        return ModelTurn(text=instruction)
    if command == "think":
        return ModelTurn(text=argument, thought=THOUGHT)
    if command == "counted":
        if tool_results:
            return ModelTurn(text=COUNTED_ANSWER)
        return ModelTurn(text=WEATHER_PREAMBLE, tool_call=(COUNT_TOOL, {"path": argument}))
    if command == "fail-after-tool":
        if tool_results:
            count = int(argument or "1")
            return ModelTurn(text=" ".join(f"word{index}" for index in range(count + 5)), fail_after=count)
        return ModelTurn(text=WEATHER_PREAMBLE, tool_call=(WEATHER_TOOL, {"city": "Kyiv"}))
    return ModelTurn(text=UNKNOWN_REPLY)
