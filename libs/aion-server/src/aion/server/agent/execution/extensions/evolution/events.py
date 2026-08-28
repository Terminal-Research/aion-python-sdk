"""Pure mappers from toolkit run events/result DTOs onto A2A task events.

Import-safe and testable without the toolkit: its typed events are
discriminated by class name and read duck-typed (phase.value, branch, call_id,
command, ...).

That is a deliberate boundary, not a technical necessity. By the time a mapper
runs the toolkit is certainly installed — the handler is pruned at startup
otherwise — so a lazy import plus `isinstance` would work at runtime. It is not
used because it would cost the mapper its tests: the toolkit ships from a
separate private repository, so every environment lacking it, CI included,
would skip the whole mapper suite instead of driving it on stand-ins. The
price is paid knowingly: matching on a name accepts any object that happens to
carry it, where `isinstance` would reject an impostor. Moving to `isinstance`
means injecting the types through an adapter and covering the real classes with
integration tests — a live option, not a closed one.

The toolkit owns all executor-stream parsing (its `CodexEventClassifier` turns
codex's `--json` envelopes into typed events); this module never sees a raw
codex event.

Three axes govern how an event crosses to A2A:

- *Typing.* The executor-stream events (`CommandStarted`, `CommandCompleted`,
  `AgentMessage`) are carried as schema-tagged data parts on the status
  message, using aion-core's published payload models — the same
  schema-tagging as the result artifact, so a programmatic consumer reads a
  typed part rather than parsing text. Every progress status also carries a
  machine-readable struct in the event `metadata` under `PROGRESS_METADATA_KEY`
  — the *whole* accumulated run state, not a per-event delta (see
  `RunProgress`). The human-facing text on the same event is what the end user
  sees.

- *Persistence.* Live progress — running commands, their results, intermediate
  agent messages — is flagged ephemeral: streamed to the client but dropped
  from task history by the task manager. Only milestones persist: branch
  resolution, the executor's final summary (`AgentMessage(final=True)`), the
  terminal status, and the `evolution-spec`/`evolution-result` artifacts.

- *Delivery.* The directive's `view` says how much of that the caller wants:
  `full` (everything, including each command's output), `activity` (the same
  chronicle minus the output — the default), `milestones` (only the durable
  events). This module shapes `full` vs `activity`, because dropping a payload
  field is the producer's job; `milestones` is the handler dropping whatever
  this module already flagged ephemeral, so the reduced view cannot drift from
  what the task record keeps.

The result artifact reuses aion-core's EvolutionResultActionPayload so the
outbound shape is the one the extension spec already defines, schema-tagged on
the part the same way inbound event parts are. The captured spec document
ships as its own markdown artifact (`SPEC_ARTIFACT_NAME`) — it is the durable
record of what the evolution planned/did/decided, readable without cloning.

Both artifacts use an id derived from the task and the artifact name rather
than a fresh uuid4, because `append_artifact_to_task` replaces an artifact
only when the id matches. A random id per emission would make a re-emitted
spec accumulate as duplicate entries instead of superseding the previous one.

A cancelled run reports through `cancel_result_message` rather than through
`result_events`: its result has to ride the terminal CANCELED status the A2A
cancel flow already owns, since anything published after a terminal state is
dropped by the event consumer.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Optional

from a2a.helpers import new_data_artifact_update_event, new_data_part
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from aion.core.a2a.extensions.behaviour_evolution import (
    EVOLUTION_VIEW_ACTIVITY,
    EVOLUTION_VIEW_FULL,
    EvolutionAgentMessagePayload,
    EvolutionCommandCompletedPayload,
    EvolutionCommandStartedPayload,
    EvolutionError,
    EvolutionResultActionPayload,
    EvolutionUsage,
)
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
)
from aion.server.a2a.utils import mark_status_event_ephemeral

if TYPE_CHECKING:
    from aion.toolkits.behaviour_evolution import EvolutionResult

logger = logging.getLogger(__name__)

__all__ = [
    "PROGRESS_METADATA_KEY",
    "RESULT_ARTIFACT_NAME",
    "SPEC_ARTIFACT_NAME",
    "RunProgress",
    "cancel_result_message",
    "event_kind_drift",
    "failed_event",
    "map_stream_event",
    "result_events",
    "status_event",
]

RESULT_ARTIFACT_NAME = "evolution-result"
SPEC_ARTIFACT_NAME = "evolution-spec"

# Key in TaskStatusUpdateEvent.metadata carrying the machine-readable progress
# struct. Namespaced by the extension URI so it can't collide with other
# metadata producers.
PROGRESS_METADATA_KEY = BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

# Bound on the command output tail carried on the completed-command payload and
# progress metadata, so a chatty command can't bloat a status event. Head+tail
# would need parsing; the tail alone is what shows a failure's cause.
_COMMAND_OUTPUT_TAIL_CHARS = 2000

# Every class name `models.EvolutionEvent` can carry. Kept in sync with the
# toolkit by `event_kind_drift`, which the handler runs once per process
# against the toolkit actually installed beside it — the only place the two
# halves are ever both present, since the toolkit ships from a separate
# (private) repository and no test environment is guaranteed to have it. The
# drift-guard test in test_evolution_events.py checks the same thing for
# whoever does have it installed, and skips otherwise.
# `map_stream_event` discriminates by name
# rather than `isinstance` because the toolkit is an optional dependency this
# module must work without — see the module docstring. `RunCompleted` is listed
# so it maps to None without a spurious warning: it carries no progress to
# surface (the handler reads the terminal result from `worker.result`, not off
# the stream), yet it is a known, expected member of the event union.
# `ExecutorTrace` is likewise known-but-dropped: reasoning, turn accounting and
# stream framing stay in the executor's logs, not in the A2A stream.
_KNOWN_EVENT_KINDS = frozenset(
    {
        "PhaseStarted",
        "BranchResolved",
        "CommandStarted",
        "CommandCompleted",
        "AgentMessage",
        "ExecutorTrace",
        "SpecCaptured",
        "RunCompleted",
    }
)

# The `stage` vocabulary this extension publishes, keyed by the toolkit phase
# each value is produced from.
#
# Declared as a table rather than passing the toolkit's `phase.value` straight
# through, even though the two currently agree token for token. The toolkit's
# phases are its own decomposition of the work and it is free to restructure
# them; `stage` is a promise to callers. Routing one through the other means a
# toolkit restructure surfaces here as a decision to make, instead of silently
# becoming a changed contract for everyone consuming the stream.
_STAGE_BY_PHASE = {
    "preparing": "preparing",
    "executing": "executing",
    "delivering": "delivering",
}

# Stage reported on the terminal event. Part of the published vocabulary above,
# but absent from the table: the toolkit sets this phase on its worker state and
# never emits a PhaseStarted for it, so nothing in the stream would otherwise
# move `stage` off the last phase the run announced.
_TERMINAL_STAGE = "reporting"

# Human-facing text per published stage. The toolkit emits the phase as a typed
# enum (a structured fact, like BranchResolved's fields); phrasing it is a
# presentation concern that lives here, not in the domain executor — so tone,
# wording and localization stay in one place and a second consumer of the
# toolkit isn't bound to this UX. The stage token still travels on the progress
# struct (what machines branch on); this is only the sentence a plain chat/A2A
# client shows the user.
#
# Each names the effect on the user's work, not the mechanism that produces it:
# a workspace, a clone, a branch and a commit are means the user never asked for
# and cannot act on. Everything an operator or a UI needs is already carried
# structurally — `branch`/`resumed`/`priorCommits` on the progress struct that
# reaches `task.metadata`, and branch/sha/PR on the result artifact — so none of
# it has to be repeated in prose.
#
# `reporting` is absent on purpose: the terminal message follows it immediately
# and says something more useful than the phase name would.
_PHASE_TEXT = {
    "preparing": "Preparing to work on your project",
    "executing": "Working on the change — planning, editing, and checking the result",
    "delivering": "Saving the result",
}

def _failed_text(result: "EvolutionResult") -> str:
    """The terminal message for a failed run.

    Leads with `error_reason` when the failing tool supplied one — a short,
    human-safe explanation (e.g. an unsupported model for the account) —
    then states what became of any work in progress, mirroring
    `cancel_result_message`'s rescue branching. `result.error` itself never
    appears here: it is the raw exception string, which for an executor
    failure carries the CLI flags it was invoked with and up to 1500
    characters of its stderr. That belongs to whoever is debugging the
    deployment, and reaches them intact on the result artifact's `error`
    field, not here.
    """
    reason = getattr(result, "error_reason", None)
    rescue_pushed = bool(getattr(result, "rescue_pushed", False))
    rescue_path = getattr(result, "rescue_path", None)
    branch = getattr(result, "branch", None)

    # Mutually exclusive: whether anything survived the failure, independent
    # of whether we also know *why* it failed.
    if rescue_pushed and branch:
        outcome_fact = f"Work completed so far is preserved on branch {branch}."
    elif rescue_path:
        # Deliberately not naming the bundle path in prose: see the identical
        # note in `cancel_result_message`.
        outcome_fact = (
            "Work completed so far was saved on the improver and needs an "
            "operator to restore it."
        )
    else:
        outcome_fact = "No changes were made."

    if reason:
        cause = reason if reason.endswith((".", "!", "?")) else f"{reason}."
        return f"Failed — {cause} {outcome_fact}"
    return f"Failed — {outcome_fact}"


class RunProgress:
    """The accumulated progress struct for one run.

    Exists because of how a status event's metadata reaches `task.metadata`:
    the task manager merges it with protobuf's `MergeFrom`, and for a
    Struct-valued key that *replaces* the value rather than merging into it.
    A per-event delta would therefore leave the durable record holding only
    whichever fields the last persisted event happened to carry — the branch
    resolved at the start would be gone by the time the run ends. So every
    event carries the full snapshot instead, and the merge becomes idempotent
    rather than lossy.

    The same shape pays off on the wire: a streaming consumer that joins late
    or drops an event still learns the run's whole state from the next event,
    instead of having to have witnessed every delta.

    Carries run-level facts only — the scope the caller asked for, the
    branch, the stage the run is at, the outcome, what it cost. Facts about a
    single event (`callId`, `exitCode`, `final`, and which kind of event it
    is) deliberately do NOT belong here: they are already on that event's
    schema-tagged payload, and publishing them twice would make two sources of
    one fact that both have to be maintained and can disagree. The payload is
    the machine-readable channel for what happened; this struct is the
    machine-readable channel for where the run stands.

    Not thread-safe and not meant to be: one instance belongs to one run's
    event loop, created by the handler and threaded through the mappers.
    """

    def __init__(self, *, scope: str) -> None:
        # `scope` is what the directive asked for (`auto`/`plan`/`implement`)
        # and is a different axis from `stage`, which is where the run has got
        # to (`preparing`/`executing`/...). Scope is fixed for the whole run;
        # stage moves. Both ride on the wire, and both were called "stage" in
        # their own vocabulary until this rename — which is exactly what made
        # them conflatable.
        # Known before the first event, so every event carries it — otherwise a
        # plan run and an implement run are indistinguishable to a consumer
        # that did not author the directive.
        self._state: dict = {"scope": scope}

    def remember(self, **fields) -> None:
        """Fold run-level facts into the accumulated state."""
        self._state.update(fields)

    def snapshot(self) -> dict:
        """A copy of the accumulated state, for one event's metadata."""
        return dict(self._state)


def _stage_for_phase(phase: str) -> str:
    """The published `stage` for a toolkit phase.

    An unmapped phase passes through under its own name and is logged: a phase
    a future toolkit adds should degrade to something a caller can see rather
    than vanish from the stream, but it is not part of the published vocabulary
    until it is named in `_STAGE_BY_PHASE`.
    """
    stage = _STAGE_BY_PHASE.get(phase)
    if stage is None:
        logger.warning(
            "evolution: toolkit phase %r has no published stage; passing it through", phase
        )
        return phase
    return stage


def _stable_artifact_id(task: Task, name: str) -> str:
    """A deterministic artifact id for `name` within `task`.

    `append_artifact_to_task` supersedes an artifact only when the incoming id
    matches one already on the task; a fresh uuid4 per emission would append a
    duplicate instead. Derived from the task id too, so two tasks in the same
    context keep their own copies.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PROGRESS_METADATA_KEY}/{task.id}/{name}"))


def _usage_payload(result: object) -> Optional[EvolutionUsage]:
    """The run's resource usage as a wire payload, or None when unreported.

    Read duck-typed like every other toolkit field here. A toolkit that reports
    no usage at all maps to None (the field then drops out of the payload
    entirely) rather than to a row of zeros, which would claim the run was free.
    """
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return EvolutionUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        # Recomputed rather than read off the toolkit's `total_tokens`
        # property, so the sum always agrees with the two fields beside it.
        total_tokens=input_tokens + output_tokens,
        requests=getattr(usage, "requests", 0) or 0,
        wall_clock_seconds=getattr(usage, "wall_clock_s", 0.0) or 0.0,
    )


def status_event(
    task: Task,
    *,
    state: "TaskState.ValueType",
    text: str | None = None,
    progress: dict | None = None,
    ephemeral: bool = False,
) -> TaskStatusUpdateEvent:
    """A status update for the task: optional agent message (what the end user
    reads) plus optional machine-readable `progress` struct (what consumers
    branch on).

    When `ephemeral`, the update is streamed to the client but kept out of task
    history (see `mark_status_event_ephemeral`). Never flag a terminal state
    ephemeral — that is the record of how the run ended.
    """
    status = TaskStatus(state=state)
    if text:
        status.message.CopyFrom(
            Message(
                context_id=task.context_id,
                task_id=task.id,
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_AGENT,
                parts=[Part(text=text)],
            )
        )
    event = TaskStatusUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        status=status,
    )
    if progress:
        event.metadata.get_or_create_struct(PROGRESS_METADATA_KEY).update(progress)
    if ephemeral:
        mark_status_event_ephemeral(event)
    return event


def failed_event(
    task: Task, *, error: str, code: str | None = None, progress: dict | None = None
) -> TaskStatusUpdateEvent:
    """Terminal FAILED update carrying the user-facing error message.

    `code` is the stable machine-readable reason (see errors.py) and rides the
    progress struct as `error.code`, so a caller branches on it instead of
    matching prose that is free to change. Nested under `error` rather than a
    flat `errorCode` key: it is terminal-only, unlike the rest of the struct's
    accumulated run progress, and nesting leaves room for sibling fields
    (e.g. a future `error.retryable`) without crowding the progress
    namespace. The rejections that happen before a run starts carry no
    accumulated progress, hence `progress` is optional.
    """
    merged = dict(progress or {})
    if code:
        merged["error"] = {"code": code}
    return status_event(
        task,
        state=TaskState.TASK_STATE_FAILED,
        text=error,
        progress=merged or None,
    )


def event_kind_drift(kinds: Iterable[str]) -> set[str]:
    """Event names the installed toolkit and `_KNOWN_EVENT_KINDS` disagree on.

    Takes the names rather than the toolkit's `EvolutionEvent` union itself, so
    this module stays importable — and testable — without the toolkit. Reading
    the union is the handler's job; it already owns loading the toolkit.

    The symmetric difference, so drift surfaces in both directions: a kind the
    toolkit added (which `map_stream_event` would drop, and only complain about
    once one had actually occurred) and one it removed or renamed (which leaves
    a branch here that can never fire again). Empty means the two agree.
    """
    return set(kinds) ^ _KNOWN_EVENT_KINDS


def map_stream_event(
    task: Task,
    event: object,
    *,
    progress: RunProgress,
    view: str = EVOLUTION_VIEW_ACTIVITY,
) -> Optional[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
    """Map one toolkit stream event onto an A2A event, or None to drop it.

    Discriminates by class name — a duck-typed boundary kept for the mapper's
    testability, not because the toolkit could be absent here; see the module
    docstring. `RunCompleted` is deliberately not handled here — the handler owns the
    terminal mapping via `result_events`. `ExecutorTrace` maps to None (dropped)
    without a warning: it is the toolkit's escape hatch for reasoning / stream
    framing this module has no reason to surface.

    `progress` accumulates the run's state across calls and is required, not
    defaulted: a caller that forgets it would silently reintroduce the
    per-event-delta behaviour `RunProgress` exists to fix.

    `view` is the caller's requested detail level. Only `EVOLUTION_VIEW_FULL`
    puts a command's output on the wire; every other view (including
    `milestones`, whose events the handler drops afterwards anyway) shapes the
    payload without it. The default matches the directive's own default, so a
    caller that never mentions `view` does not get the repository's file
    contents streamed to it.
    """
    kind = type(event).__name__
    if kind == "PhaseStarted":
        stage = _stage_for_phase(event.phase.value)
        progress.remember(stage=stage)
        return status_event(
            task,
            state=TaskState.TASK_STATE_WORKING,
            text=_PHASE_TEXT.get(stage, stage),
            progress=progress.snapshot(),
            # Narration of a phase the run has already left: live progress, not
            # a fact the durable record needs. What actually happened in each
            # phase survives in the result artifact and the final summary, so
            # persisting these would only pad the history of every evolution
            # sharing a context.
            ephemeral=True,
        )
    if kind == "BranchResolved":
        # Only a resume is worth a sentence: that this continues earlier work is
        # the one thing the user cannot infer, and it changes what they should
        # expect back. A fresh start is the default and its branch name is
        # plumbing, so that case ships no message at all — the event still
        # carries its progress struct, so the branch reaches `task.metadata`
        # either way and stays available to a UI or an operator.
        text = None
        if event.resumed:
            text = "Picking up where the previous run left off"
            if event.prior_commits:
                text = f"{text} — {event.prior_commits} change(s) already made"
        progress.remember(
            # BranchResolved is a fact *within* the PREPARING phase (see
            # worker._drive), not a phase of its own — `stage` names the
            # toolkit's actual Phase so it never claims more phases exist
            # than PhaseStarted ever announces. A consumer distinguishes
            # this from a bare PhaseStarted(preparing) by the `branch` key,
            # same pattern as `executorKind` under stage="executing".
            stage="preparing",
            branch=event.branch,
            resumed=event.resumed,
            # Note for consumers: protobuf Struct has no integer type, so this
            # arrives as a JSON number (3 becomes 3.0 when read back).
            priorCommits=event.prior_commits,
        )
        return status_event(
            task,
            state=TaskState.TASK_STATE_WORKING,
            text=text,
            progress=progress.snapshot(),
            # Deliberately NOT ephemeral: branch resolution is one of the
            # milestones this module's contract says the task record keeps (see
            # the module docstring). Because every later event repeats the
            # accumulated struct, the branch also survives in `task.metadata`
            # instead of being overwritten by the next persisted event.
        )
    if kind == "CommandStarted":
        return _command_started_event(task, event, progress=progress)
    if kind == "CommandCompleted":
        return _command_completed_event(task, event, progress=progress, view=view)
    if kind == "AgentMessage":
        return _agent_message_event(task, event, progress=progress)
    if kind == "SpecCaptured":
        return spec_artifact_event(task, path=event.path, content=event.content)
    if kind not in _KNOWN_EVENT_KINDS:
        # A toolkit event type this mapper has never heard of: either the
        # toolkit added one (this module needs a branch for it) or the two
        # sides drifted apart. Silent drop would look identical to a
        # deliberately-unsurfaced kind (e.g. ExecutorTrace) — log it so drift is
        # visible in production. This is the late signal, per event and only for
        # kinds that happen to occur; `event_kind_drift` reports the same
        # mismatch whole, at startup, before any event has to arrive.
        logger.warning("evolution: unmapped toolkit stream event %r dropped", kind)
    return None


def _typed_status_event(
    task: Task,
    *,
    text: str,
    payload,
    schema: str,
    progress: dict,
    ephemeral: bool,
) -> TaskStatusUpdateEvent:
    """A WORKING status update carrying human-facing `text`, a schema-tagged
    data part for the typed `payload`, and the machine-readable `progress`
    struct.

    The data part is schema-tagged under `EVENT_EXTENSION_URI_V1` exactly like
    the result artifact's part, so a programmatic consumer reads a typed payload
    off the message instead of parsing text. When `ephemeral`, the event is
    flagged so the task manager streams it to the client but keeps it out of
    task history (see `mark_status_event_ephemeral`).
    """
    data_part = new_data_part(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
    data_part.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({"schema": schema})
    status = TaskStatus(state=TaskState.TASK_STATE_WORKING)
    status.message.CopyFrom(
        Message(
            context_id=task.context_id,
            task_id=task.id,
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            parts=[Part(text=text), data_part],
        )
    )
    event = TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=status)
    event.metadata.get_or_create_struct(PROGRESS_METADATA_KEY).update(progress)
    if ephemeral:
        mark_status_event_ephemeral(event)
    return event


def _exit_code(event: object) -> Optional[int]:
    """The command's exit code read duck-typed, or None. `bool` is an `int`
    subclass — a stray boolean is not an exit code."""
    code = getattr(event, "exit_code", None)
    if isinstance(code, bool):
        return None
    return code if isinstance(code, int) else None


def _bounded_output(event: object) -> tuple[Optional[str], bool]:
    """The command's output tail (bounded to `_COMMAND_OUTPUT_TAIL_CHARS`) and
    whether it — or the executor upstream — was truncated. A chatty command
    must not bloat a status event; the tail is where a failure's cause shows."""
    upstream_truncated = bool(getattr(event, "truncated", False))
    output = getattr(event, "output", None)
    if not isinstance(output, str) or not output.strip():
        return None, upstream_truncated
    stripped = output.strip()
    if len(stripped) > _COMMAND_OUTPUT_TAIL_CHARS:
        return stripped[-_COMMAND_OUTPUT_TAIL_CHARS:], True
    return stripped, upstream_truncated


def _command_started_event(
    task: Task, event: object, *, progress: RunProgress
) -> TaskStatusUpdateEvent:
    """A command the executor began — ephemeral live progress."""
    progress.remember(stage="executing")
    return _typed_status_event(
        task,
        text=f"running $ {event.command}",
        payload=EvolutionCommandStartedPayload(call_id=event.call_id, command=event.command),
        schema=BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1,
        # `callId` and the command itself ride the payload above and only
        # there. The progress struct says where the run stands, not what this
        # one event is.
        progress=progress.snapshot(),
        ephemeral=True,
    )


def _command_completed_event(
    task: Task,
    event: object,
    *,
    progress: RunProgress,
    view: str = EVOLUTION_VIEW_ACTIVITY,
) -> TaskStatusUpdateEvent:
    """A command the executor finished, with its result — ephemeral live
    progress. A non-zero exit is called out in the human-facing text; the exit
    code and bounded output ride the typed payload and the progress struct.

    Outside `EVOLUTION_VIEW_FULL` the output is withheld: it is the target
    repository's content, and a caller rendering progress needs the command and
    its exit code, not what it printed. `truncated` then stays True whenever
    there *was* output, so a consumer can tell "produced nothing" from "produced
    something you were not sent" — the two are otherwise identical on the wire.
    """
    exit_code = _exit_code(event)
    output, truncated = _bounded_output(event)
    if view != EVOLUTION_VIEW_FULL:
        truncated = truncated or output is not None
        output = None
    text = f"$ {event.command}"
    if exit_code is not None and exit_code != 0:
        text = f"{text} (exit {exit_code})"
    progress.remember(stage="executing")
    # Everything about this one command — its id, its exit code, its output —
    # rides the typed payload and nothing else. The output in particular would
    # otherwise cross the wire twice for every command the executor runs, which
    # is the bulk of a run's traffic.
    return _typed_status_event(
        task,
        text=text,
        payload=EvolutionCommandCompletedPayload(
            call_id=event.call_id,
            command=event.command,
            exit_code=exit_code,
            output=output,
            truncated=truncated,
        ),
        schema=BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
        progress=progress.snapshot(),
        ephemeral=True,
    )


def _agent_message_event(
    task: Task, event: object, *, progress: RunProgress
) -> Optional[TaskStatusUpdateEvent]:
    """A natural-language message from the executor. The final summary
    (`final=True`) is the run's one durable message; intermediate messages are
    ephemeral live progress. A text-less message is dropped."""
    if not event.text:
        return None
    final = bool(getattr(event, "final", False))
    progress.remember(stage="executing")
    return _typed_status_event(
        task,
        text=event.text,
        payload=EvolutionAgentMessagePayload(text=event.text, final=final),
        schema=BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1,
        progress=progress.snapshot(),
        ephemeral=not final,
    )


def spec_artifact_event(task: Task, *, path: str, content: str) -> TaskArtifactUpdateEvent:
    """The evolution's spec document as a markdown artifact.

    Re-emitted on every run of the evolution (same name, fresh content) so the
    latest artifact always reflects the spec as committed — enough to restore
    the task's context without cloning the repo. The id is derived rather than
    random precisely so a re-emission supersedes the previous artifact instead
    of piling up beside it (see `_stable_artifact_id`).
    """
    event = TaskArtifactUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        artifact=Artifact(
            artifact_id=_stable_artifact_id(task, SPEC_ARTIFACT_NAME),
            name=SPEC_ARTIFACT_NAME,
            description=f"evolution spec ({path})",
            parts=[Part(text=content)],
        ),
        last_chunk=True,
    )
    event.artifact.metadata.get_or_create_struct(PROGRESS_METADATA_KEY).update({"path": path})
    return event


def _result_payload(result: "EvolutionResult") -> EvolutionResultActionPayload:
    """The run result as the extension's published wire payload."""
    error = (
        EvolutionError(details=result.error, reason=getattr(result, "error_reason", None))
        if result.error
        else None
    )
    return EvolutionResultActionPayload(
        outcome=result.outcome,
        branch=result.branch,
        commit_sha=result.commit_sha,
        error=error,
        summary=getattr(result, "summary", None),
        resumed=getattr(result, "resumed", False),
        commit_count=getattr(result, "commit_count", None),
        pr_url=getattr(result, "pr_url", None),
        spec_path=getattr(result, "spec_path", None),
        rescue_pushed=getattr(result, "rescue_pushed", False),
        rescue_path=getattr(result, "rescue_path", None),
        usage=_usage_payload(result),
    )


def _result_artifact_event(task: Task, result: "EvolutionResult") -> TaskArtifactUpdateEvent:
    """The result artifact alone, schema-tagged, paired with a terminal status
    event by `result_events`."""
    payload = _result_payload(result)
    artifact_event = new_data_artifact_update_event(
        task_id=task.id,
        context_id=task.context_id,
        name=RESULT_ARTIFACT_NAME,
        data=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
        last_chunk=True,
        artifact_id=_stable_artifact_id(task, RESULT_ARTIFACT_NAME),
    )
    artifact_event.artifact.parts[0].metadata.get_or_create_struct(
        EVENT_EXTENSION_URI_V1
    ).update({"schema": EvolutionResultActionPayload.SCHEMA_URI})
    return artifact_event


def cancel_result_message(task: Task, result: "EvolutionResult") -> Message:
    """The cancelled run's result, shaped for the terminal CANCELED status.

    Cancellation is the one outcome this module cannot report as an event of
    its own. The A2A cancel flow owns the terminal CANCELED, and the event
    consumer shuts its queue down the moment a terminal state lands - so an
    artifact published afterwards is dropped, not merely late. Instead the
    handler hands this message to the cancel flow, which attaches it to that
    terminal status. CANCELED is a non-COMPLETED terminal state, so the task
    manager also folds the message into task history: the rescue outcome
    becomes part of the durable record rather than something only a live
    subscriber saw.

    Carries the same schema-tagged payload the result artifact would have, so
    a consumer reads one shape for every outcome.
    """
    rescue_pushed = bool(getattr(result, "rescue_pushed", False))
    rescue_path = getattr(result, "rescue_path", None)
    branch = getattr(result, "branch", None)
    if rescue_pushed and branch:
        # The one thing worth saying: the work is not lost, and where it is.
        fact = f"Cancelled — work completed so far is preserved on branch {branch}."
    elif rescue_path:
        # Deliberately not naming the bundle path in prose: it is a filesystem
        # location on the improver's own machine, actionable by an operator and
        # nobody else. It rides the payload, where that operator reads it.
        fact = (
            "Cancelled — work completed so far was saved on the improver and "
            "needs an operator to restore it."
        )
    else:
        fact = "Cancelled."
    text = fact

    data_part = new_data_part(
        _result_payload(result).model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    data_part.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update(
        {"schema": EvolutionResultActionPayload.SCHEMA_URI}
    )
    return Message(
        context_id=task.context_id,
        task_id=task.id,
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_AGENT,
        parts=[Part(text=text), data_part],
    )


def _terminal_text(result: "EvolutionResult", location: str) -> str:
    """The terminal message: the executor's own summary, then where to look.

    `location` is the toolkit-facts line (PR link, branch name, or "No
    changes were needed") — always present. `result.summary` is the
    executor's closing explanation of what it did, when it produced one; it
    reads as the answer to "what happened", with `location` as the follow-up
    "and here's where to find it".
    """
    location = f"{location}." if not location.endswith((".", "!", "?")) else location
    summary = getattr(result, "summary", None)
    if summary:
        return f"{summary}\n\n{location}"
    return location


def result_events(
    task: Task,
    result: "EvolutionResult",
    *,
    progress: RunProgress,
) -> list[TaskArtifactUpdateEvent | TaskStatusUpdateEvent]:
    """Map a terminal EvolutionResult onto the result artifact + terminal status.

    A cancelled run maps to no events at all: cancellation only ever comes
    from the A2A cancel flow, and the executor's TaskUpdater.cancel() already
    owns the terminal CANCELED event - emitting a second terminal here would
    race it (the CANCELED is published before the worker finishes unwinding,
    so even an artifact yielded here could land after the terminal). Its
    result is delivered instead by `cancel_result_message`, on that very
    CANCELED status.
    """
    if result.outcome == "cancelled":
        return []

    # The toolkit sets REPORTING on its worker state but never emits a
    # PhaseStarted for it, so nothing else would ever move `stage` off the last
    # phase the run announced. Stated here so a finished run does not read as
    # though it were still delivering.
    progress.remember(stage=_TERMINAL_STAGE, outcome=result.outcome)
    usage = _usage_payload(result)
    if usage is not None:
        # Also on the progress struct, not only on the artifact: this is the
        # one struct that reaches `task.metadata`, so an operator auditing cost
        # finds it on the task without having to open the artifact.
        progress.remember(usage=usage.model_dump(mode="json", by_alias=True))
    terminal_progress = progress.snapshot()

    artifact_event = _result_artifact_event(task, result)
    if result.outcome == "failed":
        terminal = failed_event(task, error=_failed_text(result), progress=terminal_progress)
    elif result.outcome == "no_change":
        terminal = status_event(
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            text=_terminal_text(result, "No changes were needed"),
            progress=terminal_progress,
        )
    else:
        # Where the work can be found, in the terms the user can act on. A pull
        # request is a place to go; without one the branch is the only pointer
        # they have, so it is named — as a location, not as a git operation.
        # The commit sha is omitted: it identifies the work for machines, and
        # the artifact already carries it.
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            location = f"Done — ready for review: {pr_url}"
        elif result.branch:
            location = f"Done — changes are on branch {result.branch}"
        else:
            location = "Done"
        terminal = status_event(
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            text=_terminal_text(result, location),
            progress=terminal_progress,
        )
    return [artifact_event, terminal]
