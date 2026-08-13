"""Extract and validate the evolution directive from a routed A2A request.

The directive arrives via the event extension: the message's text part is the
free-form NL instruction, and a schema-tagged data part carries the typed
EvolutionDirectiveEventPayload (parsed upstream by the behaviour-evolution
descriptor's MessagesCollector into runtime_context.extensions). This module
is deliberately toolkit-free - it validates against aion-core models only, so
parsing stays unit-testable without the optional toolkit installed. Mapping
to toolkit domain models lives in tools_factory, behind the lazy import
boundary.

One task = one evolution step: every routed request, new or resumed, carries
its own directive event, and the durable state (branch + spec) lives in the
target repo, not in this process or in Task.metadata. The slice the caller
wants (`auto`/`plan`/`implement`) rides on the wire as `scope`; there is no
gating, pausing, or stash inside this extension — phasing a multi-run
evolution into plan-then-implement is the distributor's policy, expressed as
two separate tasks in the same context.

No secrets are ever accepted from the directive: the payload model carries
repo coordinates only, and credentials come exclusively from the handler's
own environment (see tools_factory).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from aion.core.a2a.extensions.behaviour_evolution import (
    EVOLUTION_VIEW_ACTIVITY,
    EvolutionDirectiveEventPayload,
)
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
    "ParsedDirective",
    "parse_directive",
]


@dataclass(frozen=True)
class ParsedDirective:
    """Validated inbound directive: NL instruction + typed target payload.

    `context_id` is the A2A context id of the routed task. It doubles as the
    evolution's identity in the target repo: the toolkit pins the evolution
    branch (`evolution/{context_id}`) and the spec directory to it, which is
    what makes later runs of the same context resumable.

    `scope` is the slice of the evolution the caller wants this run to cover
    (`auto`/`plan`/`implement`), taken verbatim off the wire. Not to be
    confused with the run's stage, which the extension reports as the run
    progresses: scope is fixed by the directive, stage moves.

    `view` is how much of the run's stream the caller asked to receive
    (`full`/`activity`/`milestones`), also verbatim. The payload model
    constrains it, so an unknown value is a validation error on the directive
    rather than something this side has to second-guess.
    """

    instruction: str
    context_id: str
    payload: EvolutionDirectiveEventPayload
    scope: str = "auto"
    view: str = EVOLUTION_VIEW_ACTIVITY


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
        scope=payload.scope,
        view=payload.view,
    )


def _first_text(message: Optional["Message"]) -> Optional[str]:
    """First non-empty text part - the NL instruction, per A2A convention."""
    if message is None:
        return None
    for part in message.parts:
        text = part.text.strip() if part.text else ""
        if text:
            return text
    return None
