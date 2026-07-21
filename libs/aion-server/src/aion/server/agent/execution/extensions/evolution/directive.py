"""Extract and validate the evolution directive from a routed A2A request.

The directive arrives via the event extension: the message's text part is the
free-form NL instruction, and a schema-tagged data part carries the typed
EvolutionDirectiveEventPayload (parsed upstream by the behaviour-evolution
descriptor's MessagesCollector into runtime_context.extensions). This module
is deliberately toolkit-free - it validates against aion-core models only, so
parsing stays unit-testable without the optional toolkit installed. Mapping
to toolkit domain models lives in tools_factory, behind the lazy import
boundary.

The plan gate lives here too. A directive with ``approval="required"`` splits
the evolution at the plan: the first run covers only the planning stage, the
task pauses at input_required, and the reviewer's reply decides what runs
next — approval starts the implementation run, anything else reruns planning
with the reply as feedback. The original directive survives the pause as a
stash in ``Task.metadata`` (``GATE_METADATA_KEY``): the reply message carries
no directive event, so ``parse_gated_resume`` rebuilds the directive from the
stash instead of the request. The key sits under the platform-owned
``https://docs.aion.to`` prefix: the handler emits it on the INPUT_REQUIRED
status event, and because that event flows through the *trusted-source* event
pipeline the deduplicator lets the reserved-namespace key through to
``Task.metadata`` (a public-API payload would have it stripped, so a client
cannot spoof the stash).

No secrets are ever accepted from the directive: the payload model carries
repo coordinates only, and credentials come exclusively from the handler's
own environment (see tools_factory).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from google.protobuf.json_format import MessageToDict

from aion.core.a2a.extensions.behaviour_evolution import EvolutionDirectiveEventPayload
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
)

from .errors import DirectiveError

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.types import Message
    from aion.core.runtime.context.models import AionRuntimeContext

__all__ = [
    "DirectiveError",
    "GATE_METADATA_KEY",
    "ParsedDirective",
    "gate_stash",
    "parse_directive",
    "parse_gated_resume",
]

# Task.metadata key stashing the gated directive across the input_required
# pause. Platform-owned prefix (the extension URI): the trusted-source event
# pipeline lets the handler write it, while A2ATaskDeduplicator strips it from
# public-API patches — a client cannot spoof or clobber the stash.
GATE_METADATA_KEY = f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#planGate"

# Bare replies recognised as plan approval when the reviewer's client sends
# plain text instead of a structured {"decision": ...} data part. Anything
# else is treated as revision feedback — the safe default: a misread approval
# costs one plan rerun, a misread revision would start an unapproved
# implementation.
_APPROVAL_WORDS = frozenset(
    {"approve", "approved", "lgtm", "ok", "okay", "yes", "да", "ок", "одобряю", "утверждаю"}
)


@dataclass(frozen=True)
class ParsedDirective:
    """Validated inbound directive: NL instruction + typed target payload.

    `context_id` is the A2A context id of the routed task. It doubles as the
    evolution's identity in the target repo: the toolkit pins the evolution
    branch (`evolution/{context_id}`) and the spec directory to it, which is
    what makes later runs of the same context resumable.

    `approval` is the directive's gating policy, verbatim. `stage`/`feedback`
    are per-run values the handler derives from the task lifecycle (new gated
    task -> "plan"; approved resume -> "implement"; revision resume -> "plan"
    plus the reviewer's reply as feedback) — they never arrive on the wire.
    """

    instruction: str
    context_id: str
    payload: EvolutionDirectiveEventPayload
    approval: str = "auto"
    stage: str = "auto"
    feedback: Optional[str] = None


def parse_directive(
    context: "RequestContext",
    runtime_context: Optional["AionRuntimeContext"],
) -> ParsedDirective:
    """Validate the routed request into a ParsedDirective.

    Raises:
        DirectiveError: the request carries no directive event, an event of
            an unsupported type (e.g. a verdict), a payload of the wrong
            shape, or no instruction text.
    """
    if runtime_context is None:
        raise DirectiveError("runtime context is not available for this request")

    event = runtime_context.extensions.get(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
    if event is None:
        raise DirectiveError(
            "no evolution event on the request - the directive must be delivered "
            "via the event extension as a schema-tagged data part"
        )

    if event.kind != BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1:
        raise DirectiveError(
            f"unsupported evolution event type {event.kind!r} - only directives are handled"
        )

    payload = event.payload
    if not isinstance(payload, EvolutionDirectiveEventPayload):
        raise DirectiveError("evolution directive event carries no directive payload")

    instruction = _first_text(context.message)
    if not instruction:
        raise DirectiveError("directive message carries no instruction text part")

    task = getattr(context, "current_task", None)
    context_id = (getattr(task, "context_id", "") or "").strip() if task is not None else ""
    if not context_id:
        raise DirectiveError(
            "routed task carries no context id - required as the evolution's identity"
        )

    return ParsedDirective(
        instruction=instruction,
        context_id=context_id,
        payload=payload,
        approval=getattr(payload, "approval", "auto") or "auto",
    )


def gate_stash(parsed: ParsedDirective) -> dict:
    """The directive as a Task.metadata stash surviving the approval pause.

    Only what a later run cannot recover from the resume request itself:
    the instruction and the typed payload. Never credentials — the stash is
    persisted task state.
    """
    return {
        "instruction": parsed.instruction,
        "approval": parsed.approval,
        "payload": parsed.payload.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


def parse_gated_resume(context: "RequestContext") -> Optional[ParsedDirective]:
    """Rebuild the directive for a reply on a plan-gated pause, or None.

    None means the task carries no gate stash — this resume is not a gate
    reply and the caller falls back to re-driving from a full directive.
    The reviewer's reply selects the next stage:

    - approval (a ``{"decision": "approve"}`` data part, or a bare text from
      ``_APPROVAL_WORDS``) -> ``stage="implement"``; any accompanying text
      travels as feedback ("approve, but mind X").
    - anything else -> ``stage="plan"`` with the reply text as feedback (a
      plan revision). A ``{"decision": "revise"}`` data part without text is
      a bare re-plan.

    Raises:
        DirectiveError: the stash is present but unreadable, or the reply
            carries neither a decision nor any text to act on.
    """
    task = getattr(context, "current_task", None)
    stash = _read_stash(task)
    if stash is None:
        return None

    try:
        payload = EvolutionDirectiveEventPayload.model_validate(stash["payload"])
        instruction = str(stash["instruction"])
        approval = str(stash.get("approval") or "required")
    except Exception as ex:  # noqa: BLE001 - persisted state, fail loud and precise
        raise DirectiveError(f"plan-gate stash on task is unreadable: {ex}") from ex

    decision = _reply_decision(context.message)
    text = _first_text(context.message)
    if decision is None and text is None:
        raise DirectiveError(
            "reply on a plan-gated evolution carries neither a decision data "
            "part nor any text - approve the plan or provide revision feedback"
        )

    approved = decision == "approve" or (
        decision is None and text is not None and text.lower().rstrip(".!") in _APPROVAL_WORDS
    )
    return ParsedDirective(
        instruction=instruction,
        context_id=(getattr(task, "context_id", "") or "").strip(),
        payload=payload,
        approval=approval,
        stage="implement" if approved else "plan",
        # On approval a bare keyword is not feedback; extra text rides along.
        feedback=None if approved and (text or "").lower().rstrip(".!") in _APPROVAL_WORDS else text,
    )


def _read_stash(task) -> Optional[dict]:
    """The gate stash off Task.metadata, or None when the task has none."""
    if task is None or not task.HasField("metadata") or GATE_METADATA_KEY not in task.metadata:
        return None
    value = MessageToDict(task.metadata).get(GATE_METADATA_KEY)
    return value if isinstance(value, dict) else None


def _reply_decision(message: Optional["Message"]) -> Optional[str]:
    """An explicit ``{"decision": ...}`` from the reply's data parts.

    The structured channel for UIs with approve/revise buttons; bare text
    replies fall back to keyword classification in `parse_gated_resume`.
    """
    if message is None:
        return None
    for part in message.parts:
        if not part.HasField("data"):
            continue
        try:
            value = MessageToDict(part.data)
        except Exception:  # noqa: BLE001 - a malformed part must not kill the reply
            continue
        if isinstance(value, dict):
            decision = value.get("decision")
            if decision in ("approve", "revise"):
                return decision
    return None


def _first_text(message: Optional["Message"]) -> Optional[str]:
    """First non-empty text part - the NL instruction, per A2A convention."""
    if message is None:
        return None
    for part in message.parts:
        text = part.text.strip() if part.text else ""
        if text:
            return text
    return None
