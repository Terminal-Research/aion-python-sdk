"""A2A extension models for the behaviour evolution extension.

Defines the improver's inbound directive/verdict event payloads and its
outbound result action payload. Delivered via the event extension - a
message's second (data) part carries the typed payload, schema-tagged
under params.message.parts[i].metadata[EVENT_EXTENSION_URI_V1].schema -
not a single object at params.metadata[uri]. See:
https://docs.aion.to/a2a/extensions/aion/behaviour/evolution/1.0.0
"""

from typing import ClassVar, Literal, Optional

from pydantic import Field

from aion.core.a2a import A2ABaseModel
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
    BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_VERDICT_EVENT_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1,
)

__all__ = [
    "EVOLUTION_VIEW_FULL",
    "EVOLUTION_VIEW_ACTIVITY",
    "EVOLUTION_VIEW_MILESTONES",
    "TargetContext",
    "ModelPreferences",
    "RunLimits",
    "EvolutionUsage",
    "EvolutionError",
    "EvolutionDirectiveEventPayload",
    "EvolutionVerdictEventPayload",
    "EvolutionResultActionPayload",
    "EvolutionCommandStartedPayload",
    "EvolutionCommandCompletedPayload",
    "EvolutionAgentMessagePayload",
]


# How much of a run's event stream the caller wants delivered. The three views
# nest: milestones ⊂ activity ⊂ full. Which events carry which is the improver's
# concern; what the caller promises here is only how much detail it is willing
# to receive — and, since a client that logs or forwards the stream retains
# whatever reaches it, how much of the target repo's content it takes on.

# Everything the improver produces, including the output of every shell command
# the executor runs. That output is the target repository's own content — file
# bodies, test logs — so this is the debugging view, not the default.
EVOLUTION_VIEW_FULL = "full"

# The live chronicle without the bulk data: phases, branch resolution, every
# command that starts and the exit code it ends with, the executor's messages.
# Enough to render progress; no command output.
EVOLUTION_VIEW_ACTIVITY = "activity"

# Only what survives the run in the durable task record: branch resolution, the
# final summary, the artifacts, the terminal state. What a caller that logs,
# forwards, or hands the stream to another agent should ask for.
EVOLUTION_VIEW_MILESTONES = "milestones"


class TargetContext(A2ABaseModel):
    """Repo coordinates for the agent being improved: where to clone and what to start from.

    Git coordinates only. The improver does not carry the caller's notion of a
    version: `base_ref` says where a run starts, the result's `branch` and
    `commit_sha` say where it ended, and `context_id` identifies the evolution
    those belong to. A caller that tracks versions of its own maps them to
    `context_id` on its side.
    """

    repo_url: str = Field(description="Git URL of the target agent's repository.")
    base_ref: str = Field(
        description=(
            "Git ref to clone from: a branch name, tag, or commit sha; "
            "'HEAD' uses the repository's default branch. A branch name here "
            "also becomes the base the evolution's pull request targets."
        )
    )


class ModelPreferences(A2ABaseModel):
    """What the caller would like this run's executor model to be.

    Preferences, not guarantees. The deployment owns the trust boundary - which
    endpoint is reached and whose credentials pay for it - and may pin any field
    here from its own environment, in which case what the caller asked for is
    ignored. What this does give the caller is per-run tuning without touching
    the deployment: a `scope="plan"` run and a `scope="implement"` run of the
    same evolution can ask for different models or different reasoning effort.

    Every field is optional and independent; unset means "whatever the
    deployment already resolves to".
    """

    name: Optional[str] = Field(
        default=None,
        description=(
            "Executor model to run this evolution with. Not validated here: a "
            "name the deployment's configured endpoint cannot serve surfaces as "
            "an executor failure during the run, not as a directive rejection."
        ),
    )
    reasoning_effort: Optional[str] = Field(
        default=None,
        description=(
            "Reasoning effort passed to the executor model, for models that "
            "support it. Interpreted by the engine, not by this contract."
        ),
    )
    context_window: Optional[int] = Field(
        default=None,
        description=(
            "Context window in tokens to declare for the executor model. Only "
            "needed for models the engine has no built-in knowledge of."
        ),
    )


class RunLimits(A2ABaseModel):
    """Resource ceilings the caller sets for this run's own protection.

    Not a deployment-enforced maximum - the deployment has no separate ceiling
    to fall back to or clamp against; see ModelPreferences for the analogous
    per-run vs. deployment split on model choice, where the deployment *does*
    keep a say. Every field is optional; unset means the run has no ceiling on
    that axis at all, not that a deployment default kicks in.
    """

    max_total_tokens: Optional[int] = Field(
        default=None,
        description=(
            "Token budget for the run. On reaching it, the executor stops "
            "gracefully at the next turn boundary and the run still delivers "
            "what was already committed (COMPLETED, not FAILED)."
        ),
    )
    op_timeout: Optional[float] = Field(
        default=None,
        description="Per-subprocess-operation timeout, in seconds.",
    )
    network_timeout: Optional[float] = Field(
        default=None,
        description="Timeout for network git operations, in seconds.",
    )
    codex_timeout: Optional[float] = Field(
        default=None,
        description=(
            "Wall-clock ceiling for one whole executor call, in seconds. Falls "
            "back to `op_timeout` when unset."
        ),
    )


class EvolutionDirectiveEventPayload(A2ABaseModel):
    """Inbound improvement command from the control plane to the improver.

    Delivered as the second (data) part of an A2A message; the first part
    is a free-form natural-language instruction, per A2A convention. The
    target's own self-context is not included here - the improver reads
    it from aion.yaml in the target repo after cloning.
    """

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1
    EVENT_TYPE: ClassVar[str] = BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1

    target: TargetContext = Field(description="Repo coordinates for the agent being improved.")
    kind: Literal["feature", "bugfix"] = Field(
        description="Nature of the change: new capability, or a fix to incorrect behaviour."
    )
    mode: Literal["advisory", "directive"] = Field(
        description="Autonomy level: 'advisory' allows a no_change outcome; 'directive' requires a change."
    )
    scope: Literal["auto", "plan", "implement"] = Field(
        default="auto",
        description=(
            "Which slice of the evolution this run covers. 'auto' runs "
            "planning and implementation in one run. 'plan' produces only "
            "the evolution spec and subtask plan, without implementing it. "
            "'implement' executes the already-approved plan without "
            "re-planning. Phasing and gating between runs is the "
            "distributor's policy - the improver does not pause a run to "
            "wait on this field. Distinct from the run's stage, which the "
            "improver reports as the run moves through preparing, executing, "
            "delivering and reporting: this is what was asked for, that is "
            "where the run stands."
        ),
    )
    model: Optional[ModelPreferences] = Field(
        default=None,
        description=(
            "Per-run executor model tuning. Omit to use whatever the deployment "
            "resolves on its own. The deployment's own environment overrides "
            "anything set here - see ModelPreferences."
        ),
    )
    branch_strategy: Optional[Literal["beta-branch", "pull-request"]] = Field(
        default=None,
        description=(
            "Delivery strategy for this run: push only ('beta-branch') or also "
            "open/reuse a pull request ('pull-request'). Omit to use the "
            "deployment's aion.yaml default."
        ),
    )
    limits: Optional[RunLimits] = Field(
        default=None,
        description=(
            "Resource ceilings this run's caller sets for its own protection. "
            "See RunLimits - unlike `model`, the deployment has no separate "
            "ceiling to fall back to here."
        ),
    )
    view: Literal["full", "activity", "milestones"] = Field(
        default=EVOLUTION_VIEW_ACTIVITY,
        description=(
            "How much of the run's event stream to deliver. 'activity' - the "
            "default - carries the live chronicle without command output: "
            "phases, each command and the exit code it ended with, the "
            "executor's messages. 'full' adds the output of every command, "
            "which is the target repository's own content; ask for it to debug "
            "a deployment. 'milestones' carries only what the durable task "
            "record keeps, for a caller that logs or forwards the stream. This "
            "bounds delivery, not access: the terminal result and its artifacts "
            "arrive in every view, and even 'activity' carries command lines "
            "and executor prose that can quote the repository."
        ),
    )


class EvolutionVerdictEventPayload(A2ABaseModel):
    """Inbound verdict from an independent verifier, delivered after the beta is deployed.

    Delivered as the second (data) part of an A2A message; the first part
    is the verifier's free-form assessment. The improver never issues the
    final verdict on its own beta - this always arrives from outside.

    What the verdict applies to is carried by the message's context, not by a
    field here: the evolution's `context_id` is the same identity its directive
    used, and the run's `branch`/`commit_sha` name the beta itself.
    """

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_VERDICT_EVENT_PAYLOAD_SCHEMA_V1
    EVENT_TYPE: ClassVar[str] = BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1

    verdict: Literal["approve", "reject", "needs-changes"] = Field(
        description="Branching signal: promote the beta, roll back, or start a new run."
    )
    verifier_id: str = Field(
        description="Identity of the independent verifier that issued this verdict."
    )


class EvolutionUsage(A2ABaseModel):
    """Resources a run consumed, as reported by the executor.

    Accumulated across every executor turn of the run - including the turns of
    earlier runs' commits only insofar as they cost this run nothing, i.e. not
    at all: this is what *this* run spent, not the evolution's lifetime total.
    A caller aggregating an evolution's cost sums this across the runs of a
    context.

    All axes default to zero because engines report different subsets. Zero is
    not distinguishable from unmeasured: a run that failed before invoking the
    executor and a run whose engine reported no usage both read as zeros. Treat
    these as "what the improver was able to account for", not as a bill.
    """

    input_tokens: int = Field(default=0, description="Tokens fed into the model across the run.")
    output_tokens: int = Field(default=0, description="Tokens generated by the model across the run.")
    total_tokens: int = Field(
        default=0,
        description=(
            "Sum of input and output tokens. Restated rather than left to the "
            "caller so every consumer reports the same number - the same "
            "deliberate redundancy as EvolutionAgentMessagePayload.text."
        ),
    )
    requests: int = Field(
        default=0,
        description="Executor invocations made (one per `codex exec` call), not model turns.",
    )
    wall_clock_seconds: float = Field(
        default=0.0, description="Elapsed time spent inside executor calls, in seconds."
    )


class EvolutionError(A2ABaseModel):
    """Why a run failed, as two axes rather than two unrelated fields.

    Grouped under one `error` object — like the terminal status event's
    `error.code` — so a future field (e.g. a stable code paralleling that
    one) has an obvious home instead of becoming a third top-level
    `error*` field on the result payload.
    """

    details: str = Field(
        description=(
            "Full diagnostic string: argv, stderr, paths - whatever the failing "
            "step captured. For whoever debugs the run, not for display to "
            "whoever asked for the change."
        )
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Short, human-safe explanation of the failure, set only when the "
            "failing tool itself supplied one. Unlike `details`, this never "
            "carries paths, argv, or stderr, so a UI can show it to whoever asked "
            "for the change."
        ),
    )


class EvolutionResultActionPayload(A2ABaseModel):
    """Outbound run result reported by the improver once a run completes."""

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1

    outcome: Literal["succeeded", "failed", "no_change", "cancelled"] = Field(
        description=(
            "Run outcome. 'no_change' is only valid when the directive's mode is "
            "'advisory'. 'cancelled' reports a run stopped by the caller: it is "
            "delivered on the terminal CANCELED status rather than as a result "
            "artifact, and its `rescue*` fields say what became of the work the "
            "run had already committed."
        )
    )
    branch: Optional[str] = Field(default=None, description="Beta branch in the target repo.")
    commit_sha: Optional[str] = Field(
        default=None, description="Exact commit pinning the beta artifact."
    )
    error: Optional[EvolutionError] = Field(
        default=None, description="Why the run failed; populated when outcome is 'failed'."
    )
    summary: Optional[str] = Field(
        default=None, description="The executor's own closing summary of what it did, if any."
    )
    resumed: bool = Field(
        default=False,
        description="True when this run continued the evolution's existing branch.",
    )
    commit_count: Optional[int] = Field(
        default=None,
        description="Commits on the evolution branch since its base, across all runs.",
    )
    pr_url: Optional[str] = Field(
        default=None, description="URL of the pull request opened for the branch, if any."
    )
    spec_path: Optional[str] = Field(
        default=None,
        description="Repo-relative path of the evolution's spec document, if captured.",
    )
    rescue_pushed: bool = Field(
        default=False,
        description=(
            "True when a failed run's committed-but-undelivered work was rescued by "
            "pushing the evolution branch: the work is durable in the target repo and "
            "the next run of this context resumes on it automatically."
        ),
    )
    # No `rescue_path` field: a rescue that could not be delivered by pushing
    # falls back to a git bundle, but that bundle is written to a temp file on
    # the improver's own ephemeral disk — a location nothing outside that
    # process's lifetime could ever reach, so a path naming it would be dead
    # information from the moment it is read. When the bundle is small enough
    # (see the improver's own size ceiling), it instead rides the task as its
    # own artifact, named `rescue-{context_id}.bundle` — that artifact's
    # presence is the signal a consumer acts on, not a field here.
    rescue_bundle_created: bool = Field(
        default=False,
        description=(
            "True as soon as the rescue fallback produced a git bundle, whether or "
            "not it was small enough to attach as the `rescue-{context_id}.bundle` "
            "artifact. Distinguishes 'nothing survived the failure' (False) from "
            "'work survived but could not be handed off' (True, with no matching "
            "artifact — the bundle exceeded the improver's size ceiling)."
        ),
    )
    usage: Optional[EvolutionUsage] = Field(
        default=None,
        description=(
            "Resources this run consumed. Absent only from an improver that does "
            "not account for usage at all; an improver that does always reports "
            "the object, using zeros for what it could not measure."
        ),
    )


class EvolutionCommandStartedPayload(A2ABaseModel):
    """A shell command the executor has begun running, reported mid-run.

    Streamed to the client for live progress but not persisted in task history
    (see the improver's events.py). Correlate with the matching
    EvolutionCommandCompletedPayload by ``call_id`` — a started payload with no
    completed counterpart marks a command still running (or a hung run).
    """

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1

    call_id: str = Field(
        description="Correlates this start with its EvolutionCommandCompletedPayload."
    )
    command: str = Field(description="The shell command line the executor started running.")


class EvolutionCommandCompletedPayload(A2ABaseModel):
    """A shell command the executor finished running, with its result.

    Streamed to the client for live progress but not persisted in task history.
    Carries the exit code and (bounded) output the started payload cannot: a
    consumer tells a passing command from a failing one off ``exit_code``.
    """

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1

    call_id: str = Field(
        description="Correlates this completion with its EvolutionCommandStartedPayload."
    )
    command: str = Field(description="The shell command line that was executed.")
    exit_code: Optional[int] = Field(
        default=None,
        description="Process exit code; None when the executor did not report one.",
    )
    output: Optional[str] = Field(
        default=None,
        description="Command output tail, bounded by the improver before it is sent.",
    )
    truncated: bool = Field(
        default=False,
        description="True when the output was truncated to fit the size bound.",
    )


class EvolutionAgentMessagePayload(A2ABaseModel):
    """A natural-language message from the executor during a run.

    ``text`` repeats the message's first (text) part on purpose. A status event
    carries both representations — prose for the end user, a schema-tagged
    payload for programmatic consumers — and the contract is that a consumer
    reads the payload *instead of* parsing the prose. Dropping ``text`` here
    would leave such a consumer with framing and no message, and would break the
    symmetry with the command payloads, whose ``command`` likewise restates what
    the text renders.

    Intermediate messages (``final=False``) are streamed but not persisted; the
    single ``final=True`` message is the run's durable summary and is kept in
    task history.
    """

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1

    text: str = Field(description="The executor's message text.")
    final: bool = Field(
        default=False,
        description="True for the last message of the run (the durable summary).",
    )
