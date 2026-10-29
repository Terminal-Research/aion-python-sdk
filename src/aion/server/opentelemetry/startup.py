"""Bounded startup spans and explicit context transfer to managed processes."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from collections.abc import Iterator

from opentelemetry import context, trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .tracing import init_tracing

logger = logging.getLogger(__name__)
_propagator = TraceContextTextMapPropagator()


def startup_carrier() -> dict[str, str]:
    """Serialize the current trace for one process launch, without baggage."""
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier


@contextmanager
def without_trace() -> Iterator[None]:
    """Keep ongoing work out of startup while preserving other contextvars."""
    token = context.attach(context.Context())
    try:
        yield
    finally:
        context.detach(token)


@contextmanager
def startup_phase(name: str, parent: context.Context | None = None) -> Iterator[trace.Span]:
    """Attribute one awaited initialization phase to its current or explicit parent.

    Args:
        name: Phase name shown in logs and exported spans.
        parent: Explicit parent when the phase runs in a different asyncio task.
    """
    init_tracing()
    with trace.get_tracer(__name__).start_as_current_span(name, context=parent) as span:
        yield span


class StartupSpan:
    """Own one startup attempt independently of the server's running lifetime.

    Args:
        name: Lifecycle name, such as ``application.startup`` or ``agent.startup``.
        carrier: Parent serialized by the launcher; absent for a fresh startup.
    """

    def __init__(self, name: str, carrier: dict[str, str] | None = None):
        init_tracing()
        self.name = name
        self.finished = False
        # Outcome passed to ``finish``: tells a later error while serving
        # apart from the startup failure it already reported.
        self.outcome: str | None = None
        # An empty explicit context also prevents forked processes from using
        # an inherited current span when this launch has no parent.
        parent = _propagator.extract(carrier or {}, context=context.Context())
        self.span = trace.get_tracer(__name__).start_span(name, context=parent)
        self.context = trace.set_span_in_context(self.span, context.Context())

    @contextmanager
    def activate(self) -> Iterator[None]:
        """Make this attempt current only while performing initialization."""
        token = context.attach(self.context)
        try:
            yield
        finally:
            context.detach(token)

    def finish(self, outcome: str = "ready", error: BaseException | None = None) -> None:
        """Log and end the attempt once, including failures returned as values.

        Args:
            outcome: ``ready``, ``failed``, or ``launched`` when readiness was skipped.
            error: Startup exception, when available.
        """
        if self.finished:
            return
        with self.activate():
            if outcome == "failed":
                self.span.set_status(trace.StatusCode.ERROR)
                if error is not None:
                    self.span.record_exception(error)
                logger.error("%s failed", self.name, exc_info=error)
            else:
                if outcome == "ready":
                    self.span.set_status(trace.StatusCode.OK)
                logger.info("%s %s", self.name, outcome)
        self.span.end()
        self.finished = True
        self.outcome = outcome
