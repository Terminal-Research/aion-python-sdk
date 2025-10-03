import logging
from dataclasses import dataclass
from typing import Optional

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.trace import SpanContext, TraceFlags, NonRecordingSpan, INVALID_SPAN_ID


def init_tracing():
    """
    Initialize global tracing provider.

    Sets up the base tracer provider for the application.
    Call once at application startup. For multiprocessing, call in each process.
    """
    provider = TracerProvider()
    trace.set_tracer_provider(provider)


def generate_request_span_context(
        trace_id: Optional[int] = None,
        span_id: Optional[int] = None
) -> Optional[Context]:
    """
    Generate a remote span context for continuing an existing trace.

    Args:
        trace_id: The trace ID (128-bit integer) to continue
        span_id: Optional parent span ID (64-bit integer).
                 If None, a random one will be generated.

    Returns:
        Context object that can be passed to start_as_current_span
    """
    if trace_id is None:
        return None

    try:
        if span_id is None:
            span_id = INVALID_SPAN_ID

        span_context = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=True,
            trace_flags=TraceFlags(0x01)  # Sampled
        )

        parent = NonRecordingSpan(span_context)
        return trace.set_span_in_context(parent)
    except Exception as ex:
        logging.exception(f"Failed to generate span context: {ex}")
        return None


@dataclass
class SpanInfo:
    trace_id: Optional[int] = None
    span_id: Optional[int] = None
    span_name: Optional[str] = None
    parent_span_id: Optional[int] = None


def get_span_info(span=None) -> SpanInfo:
    """
    Get full span information including parent span ID

    Args:
        span: Span to inspect. If None, uses current span.

    Returns:
        dict with span_id, trace_id, parent_span_id, name
    """
    if span is None:
        span = trace.get_current_span()

    span_ctx = span.get_span_context()

    span_info = SpanInfo(
        trace_id=span_ctx.trace_id or None,
        span_id=span_ctx.span_id or None,
        span_name=getattr(span, "name", None)
    )

    # Try to get parent span ID (SDK only)
    if isinstance(span, ReadableSpan):
        parent = span.parent
        if parent:
            span_info.parent_span_id = parent.span_id

    return span_info
