"""The command contract every scenario agent implements.

One registry, shared by the agents and by the tests. The agents read it to
know what to answer; the tests read it to know what to expect. A behaviour
that differs between frameworks belongs in ``frameworks.UNSUPPORTED``, never
in a branch here.

Wire format: the first word of the inbound text is the command key, the rest
is its argument. An unknown key answers with the menu, the same text ``help``
answers with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel

__all__ = [
    "Command",
    "COMMANDS",
    "COMMANDS_BY_KEY",
    "ParsedCommand",
    "parse_command",
    "help_text",
    "echo_text",
    "slow_text",
    "DEFAULT_SLOW_SECONDS",
    "stream_chunks",
    "stream_text",
    "step_text",
    "steps_texts",
    "ids_text",
    "big_text",
    "ask_question",
    "ask_answer_text",
    "ask_twice_questions",
    "ask_twice_answer_text",
    "NOT_IMPLEMENTED_PREFIX",
    "not_implemented_text",
    "IdsResponse",
    "ExtResponse",
    "WhoamiResponse",
    "EventResponse",
    "ConfigResponse",
    "PartsResponse",
    "parts_text",
    "ARTIFACT_DATA_NAME",
    "ARTIFACT_FILE_NAME",
    "ARTIFACT_URL_NAME",
    "ARTIFACT_FILE_BYTES",
    "ARTIFACT_FILE_MEDIA_TYPE",
    "ARTIFACT_URL",
    "artifacts_data_parts",
    "artifacts_text",
    "OUTBOX_MESSAGE_TEXT",
    "OUTBOX_TASK_HISTORY_TEXT",
    "OUTBOX_TASK_ARTIFACT_NAME",
    "OUTBOX_TASK_ARTIFACT_TEXT",
    "OUTBOX_TASK_METADATA",
    "FAIL_MODES",
    "FAIL_REPLY_TEXT",
    "FAIL_MESSAGE",
]


# --------------------------------------------------------------------------
# Structured answers
#
# A command whose answer is compared field by field replies with the JSON of
# one of these models in a single message. The model is the contract: the
# agent builds it, the test parses with it.
# --------------------------------------------------------------------------

class IdsResponse(BaseModel):
    """Task and context identity as the agent sees it."""

    task_id: Optional[str] = None
    context_id: Optional[str] = None


class ExtResponse(BaseModel):
    """What the request's extension activation looks like inside the agent."""

    active: list[str] = []
    unknown: list[str] = []
    event_kind: Optional[str] = None
    event_id: Optional[str] = None
    has_distribution: bool = False


class WhoamiResponse(BaseModel):
    """Identity carried by the daemon extension payload."""

    daemon_identity_id: Optional[str] = None
    requester_identity_id: Optional[str] = None
    environment: Optional[str] = None
    behavior: Optional[str] = None


class EventResponse(BaseModel):
    """What the event carried, and which router handler it reached.

    ``handler`` is the name of the handler the SDK's event router picked, and
    is absent on a framework whose adapter has no router: the delivery of the
    event is the contract both frameworks share, the routing is one
    framework's.
    """

    kind: Optional[str] = None
    event_id: Optional[str] = None
    payload: dict = {}
    handler: Optional[str] = None


class ConfigResponse(BaseModel):
    """One configuration key as resolved by the running agent."""

    key: str
    present: bool
    value: Optional[str] = None


class PartsResponse(BaseModel):
    """The inbound message as the agent saw it: one entry per part, in order.

    ``kinds`` names the content each part carried - ``text``, ``raw``, ``url``
    or ``data`` - which is how a scenario tells whether the server converted
    an inline file before the agent ran.
    """

    kinds: list[str] = []
    urls: list[str] = []
    raw_bytes: int = 0


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Command:
    """One command in the contract.

    Attributes:
        key: First word of the inbound text.
        summary: One line, as it appears in the menu.
        argument_hint: Placeholder shown in the menu; empty when the command
            takes no argument.
        tags: pytest markers the scenarios for this command carry.
        response_model: Model the answer parses into, when the answer is
            structured JSON rather than prose.
    """

    key: str
    summary: str
    argument_hint: str = ""
    tags: tuple[str, ...] = ()
    response_model: Optional[type[BaseModel]] = field(default=None)

    @property
    def usage(self) -> str:
        """The command as written in the menu, argument placeholder included."""
        return f"{self.key} {self.argument_hint}".strip()


COMMANDS: tuple[Command, ...] = (
    Command("help", "Show this menu", tags=("smoke",)),
    Command("echo", "Reply with the argument, unchanged", "<text>", ("smoke", "events")),
    Command("stream", "Reply in n chunks of one message", "<n>", ("streaming",)),
    Command("typing", "Send an ephemeral typing status, then a reply", tags=("streaming", "events")),
    Command("steps", "Emit n working statuses, then complete", "<n>", ("events",)),
    Command("slow", "Reply after n seconds", "<sec>", ("lifecycle",)),
    Command("artifacts", "Emit a two-part data artifact, an inline file and a url",
            tags=("artifacts", "files")),
    Command("card", "Emit one card", tags=("artifacts",)),
    Command("outbox-task", "Return an A2A Task through the outbox", tags=("events",)),
    Command("outbox-message", "Return an A2A Message through the outbox", tags=("events",)),
    Command("ask", "Ask a question, then quote the answer", tags=("interrupts",)),
    Command("ask-twice", "Ask two questions in a row", tags=("interrupts",)),
    Command("fail", "Fail on purpose: exception | after-reply", "<mode>",
            ("errors", "terminal_states")),
    Command("ext", "Report active and unknown extensions", tags=("extensions",),
            response_model=ExtResponse),
    Command("whoami", "Report the daemon identity", tags=("daemon",), response_model=WhoamiResponse),
    Command("event", "Report the event this turn carried", tags=("extensions", "events"),
            response_model=EventResponse),
    Command("config", "Report one configuration value", "<key>", ("config",),
            response_model=ConfigResponse),
    Command("ids", "Report task and context ids", tags=("events", "lifecycle"),
            response_model=IdsResponse),
    Command("big", "Reply with n KB of text", "<kb>", ("errors",)),
    Command("parts", "Report the kinds of parts the inbound message carried", tags=("files",),
            response_model=PartsResponse),
)

COMMANDS_BY_KEY: dict[str, Command] = {command.key: command for command in COMMANDS}


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedCommand:
    """An inbound text split into its command key and argument."""

    key: str
    argument: str
    known: bool

    def int_argument(self, default: int) -> int:
        """The argument as an int, or ``default`` when it is absent or not a number."""
        try:
            return int(self.argument.strip())
        except (TypeError, ValueError):
            return default

    def float_argument(self, default: float) -> float:
        """The argument as a float, or ``default`` when it is absent or not a number."""
        try:
            return float(self.argument.strip())
        except (TypeError, ValueError):
            return default


def parse_command(text: Optional[str]) -> ParsedCommand:
    """Split inbound text into a command key and its argument."""
    key, _, argument = (text or "").strip().partition(" ")
    key = key.strip()
    return ParsedCommand(key=key, argument=argument.strip(), known=key in COMMANDS_BY_KEY)


# --------------------------------------------------------------------------
# Expected answers
#
# Every deterministic string an agent sends is built here, so the agent and
# the assertion cannot drift apart.
# --------------------------------------------------------------------------

MENU_HEADER = "Scenario commands:"


def help_text() -> str:
    """The menu, answered by ``help`` and by any unknown command."""
    width = max(len(command.usage) for command in COMMANDS)
    lines = [MENU_HEADER]
    lines.extend(f"  {command.usage.ljust(width)}  {command.summary}" for command in COMMANDS)
    return "\n".join(lines)


def echo_text(argument: str) -> str:
    """What ``echo <text>`` answers: the argument and nothing else."""
    return argument


DEFAULT_SLOW_SECONDS = 10


def slow_text(seconds: float) -> str:
    """What ``slow <sec>`` answers after sleeping."""
    return f"slept {seconds}s"


def stream_chunks(count: int) -> tuple[str, ...]:
    """The chunks ``stream <n>`` sends, in order."""
    return tuple(f"[chunk {index}/{count}]" for index in range(1, count + 1))


def stream_text(count: int) -> str:
    """The whole message ``stream <n>`` adds up to."""
    return "".join(stream_chunks(count))


def step_text(index: int, count: int) -> str:
    """The text of one working status emitted by ``steps <n>``."""
    return f"step {index}/{count}"


def steps_texts(count: int) -> tuple[str, ...]:
    """Every working status ``steps <n>`` emits, in order."""
    return tuple(step_text(index, count) for index in range(1, count + 1))


def ids_text(task_id: Optional[str], context_id: Optional[str]) -> str:
    """The JSON ``ids`` answers with."""
    return IdsResponse(task_id=task_id, context_id=context_id).model_dump_json()


def parts_text(kinds: list[str], urls: list[str], raw_bytes: int) -> str:
    """The JSON ``parts`` answers with."""
    return PartsResponse(kinds=kinds, urls=urls, raw_bytes=raw_bytes).model_dump_json()


# What ``artifacts`` emits, in order: a data artifact of two parts, an inline
# file, and a url. The file is the one the server may convert; the other two
# are there to show it converts nothing else.
ARTIFACT_DATA_NAME = "data"
ARTIFACT_FILE_NAME = "file"
ARTIFACT_URL_NAME = "url"
ARTIFACT_FILE_BYTES = b"scenario file content\n"
ARTIFACT_FILE_MEDIA_TYPE = "text/plain"
ARTIFACT_URL = "https://example.invalid/scenario-file.txt"


def artifacts_data_parts() -> tuple[dict, dict]:
    """The two structured parts of the data artifact ``artifacts`` emits."""
    return ({"index": 1, "label": "first"}, {"index": 2, "label": "second"})


def artifacts_text() -> str:
    """What ``artifacts`` says once every artifact is out."""
    return "3 artifacts sent"


BIG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def big_text(kilobytes: int) -> str:
    """The filler ``big <kb>`` answers with: exactly ``kb`` * 1024 characters."""
    size = max(0, kilobytes) * 1024
    repeats = size // len(BIG_ALPHABET) + 1
    return (BIG_ALPHABET * repeats)[:size]


ASK_QUESTION = "What should I use?"


def ask_question() -> str:
    """The question ``ask`` interrupts with."""
    return ASK_QUESTION


def ask_answer_text(answer: str) -> str:
    """What ``ask`` answers once the user has replied."""
    return f"you said: {answer}"


def ask_twice_questions() -> tuple[str, str]:
    """The two questions ``ask-twice`` interrupts with, in order."""
    return ("First of two: what should I use?", "Second of two: are you sure?")


def ask_twice_answer_text(first: str, second: str) -> str:
    """What ``ask-twice`` answers once both questions have been answered.

    Both answers, in the order they were given: which one arrived first is
    the part a resumed interrupt can get wrong.
    """
    return f"1: {first}, 2: {second}"


# What the outbox commands put into the A2A payload they return. The point of
# these two is that the agent hands the server a finished A2A object instead
# of speaking through the thread, so every value here is one a scenario looks
# for in the task the server stored, not only in the stream.
OUTBOX_MESSAGE_TEXT = "outbox message"
OUTBOX_TASK_HISTORY_TEXT = "outbox task history"
OUTBOX_TASK_ARTIFACT_NAME = "outbox-artifact"
OUTBOX_TASK_ARTIFACT_TEXT = "outbox artifact content"
OUTBOX_TASK_METADATA = {"scenario": "outbox-task"}


# How `fail <mode>` fails: before saying anything, or with a reply already
# delivered. Those are the two an agent can produce.
#
# The third case the server handles - a producer that crashes after its own
# terminal state was already reported, which must not be overwritten with
# FAILED - is not one of these. No authoring surface lets an agent report a
# terminal state: `a2a_outbox` is read from the final state, which a crash
# never reaches. It is a unit test on the executor instead
# (tests/unit/server/agent/test_failed_task_closing.py).
FAIL_MODES = ("exception", "after-reply")
FAIL_REPLY_TEXT = "about to fail"
FAIL_MESSAGE = "scenario failure"


NOT_IMPLEMENTED_PREFIX = "not implemented: "


def not_implemented_text(key: str) -> str:
    """Answer of an agent that knows the command but does not implement it yet.

    A registry entry with no behaviour answers this rather than falling through
    to the menu, so ``test_contract`` reports a gap instead of a wrong menu.
    """
    return f"{NOT_IMPLEMENTED_PREFIX}{key}"
