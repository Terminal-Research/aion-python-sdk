from dataclasses import dataclass
from typing import Optional, Union

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
        trace_id: Optional[Union[int, str]] = None,
        span_id: Optional[Union[int, str]] = None
) -> Optional[Context]:
    """
    Generate a remote span context for continuing an existing trace.

    Args:
        trace_id: The trace ID as 128-bit (16 byte) integer or hex string to continue
        span_id: Optional parent span ID as 64-bit (8 byte) integer or hex string.
                 If None, INVALID_SPAN_ID will be used.

    Returns:
        Context object that can be passed to start_as_current_span
    """
    if trace_id is None:
        return None

    from aion.shared.logging.factory import get_logger
    logger = get_logger()

    try:
        # Convert trace_id from hex string to int if needed
        if isinstance(trace_id, str):
            try:
                trace_id = int(trace_id, 16)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Failed to convert trace_id '{trace_id}' from hex to int: {e}. "
                    f"Tracing will not be linked to parent trace."
                )
                return None

        # Convert span_id from hex string to int if needed
        if span_id is None:
            span_id = INVALID_SPAN_ID

        elif isinstance(span_id, str):
            try:
                span_id = int(span_id, 8)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Failed to convert span_id '{span_id}' from hex to int: {e}. "
                    f"Using INVALID_SPAN_ID instead."
                )
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
        logger.exception(f"Failed to generate span context: {ex}")
        return None


@dataclass
class SpanInfo:
    """Container for OpenTelemetry span information with lazy hex conversion.

    Stores trace and span IDs in both integer and hexadecimal formats,
    converting to hex representation only when needed.
    """
    trace_id: Optional[int] = None
    span_id: Optional[int] = None
    span_name: Optional[str] = None
    parent_span_id: Optional[int] = None
    _trace_id_hex: Optional[bytes] = None
    _span_id_hex: Optional[bytes] = None
    _parent_span_id_hex: Optional[bytes] = None

    @property
    def trace_id_hex(self) -> bytes:
        if self._trace_id_hex is None and self.trace_id is not None:
            self._trace_id_hex = self.trace_id.to_bytes(16, "big").hex()
        return self._trace_id_hex

    @property
    def span_id_hex(self) -> bytes:
        if self._span_id_hex is None and self.span_id is not None:
            self._span_id_hex = self.span_id.to_bytes(8, "big").hex()
        return self._span_id_hex

    @property
    def parent_span_id_hex(self) -> bytes:
        if self._parent_span_id_hex is None and self.parent_span_id is not None:
            self._parent_span_id_hex = self.parent_span_id.to_bytes(8, "big").hex()
        return self._parent_span_id_hex


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
