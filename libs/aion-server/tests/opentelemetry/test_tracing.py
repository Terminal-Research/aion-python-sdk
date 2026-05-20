"""Tests for aion.shared.opentelemetry.tracing.

Focus areas:
  SpanInfo:
    - trace_id_hex lazy hex conversion (16-byte big-endian)
    - span_id_hex lazy hex conversion (8-byte big-endian)
    - parent_span_id_hex lazy hex conversion
    - None fields return None for all hex properties
    - Hex results cached (computed only once)

  get_span_info():
    - Uses current span when span=None
    - Returns SpanInfo with trace_id and span_id from span context
    - trace_id=0 stored as None
    - span_id=0 stored as None
    - parent_span_id populated for ReadableSpan with parent

  generate_request_span_context():
    - Returns None for trace_id=None
    - Returns Context for integer trace_id
    - Returns Context for hex string trace_id
    - Returns None for invalid hex string (logs warning)
    - span_id=None uses INVALID_SPAN_ID (no error)
    - Hex string span_id is converted correctly
    - Invalid hex span_id falls back to INVALID_SPAN_ID (logs warning)
    - Unexpected exception during SpanContext construction returns None

  init_tracing():
    - Sets a TracerProvider on global trace module
"""

from unittest.mock import MagicMock, patch, call

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.trace import INVALID_SPAN_ID, NonRecordingSpan, SpanContext, TraceFlags

from aion.server.opentelemetry.tracing import (
    SpanInfo,
    generate_request_span_context,
    get_span_info,
    init_tracing,
)

class TestSpanInfoHexConversion:
    def test_trace_id_hex_16_bytes(self):
        """trace_id_hex converts trace_id to 16-byte big-endian hex string."""
        info = SpanInfo(trace_id=1)
        assert info.trace_id_hex == (1).to_bytes(16, "big").hex()

    def test_trace_id_hex_known_value(self):
        """trace_id_hex produces correct hex for known integer values."""
        val = 0x0102030405060708090a0b0c0d0e0f10
        info = SpanInfo(trace_id=val)
        assert info.trace_id_hex == "0102030405060708090a0b0c0d0e0f10"

    def test_span_id_hex_8_bytes(self):
        """span_id_hex converts span_id to 8-byte big-endian hex string."""
        info = SpanInfo(span_id=255)
        assert info.span_id_hex == (255).to_bytes(8, "big").hex()

    def test_span_id_hex_known_value(self):
        """span_id_hex produces correct hex for known integer values."""
        val = 0x0102030405060708
        info = SpanInfo(span_id=val)
        assert info.span_id_hex == "0102030405060708"

    def test_parent_span_id_hex_8_bytes(self):
        """parent_span_id_hex converts to 8-byte big-endian hex string."""
        info = SpanInfo(parent_span_id=1)
        assert info.parent_span_id_hex == (1).to_bytes(8, "big").hex()

    def test_trace_id_none_returns_none(self):
        """trace_id_hex returns None when trace_id is None."""
        info = SpanInfo()
        assert info.trace_id_hex is None

    def test_span_id_none_returns_none(self):
        """span_id_hex returns None when span_id is None."""
        info = SpanInfo()
        assert info.span_id_hex is None

    def test_parent_span_id_none_returns_none(self):
        """parent_span_id_hex returns None when parent_span_id is None."""
        info = SpanInfo()
        assert info.parent_span_id_hex is None

    def test_trace_id_hex_cached(self):
        """trace_id_hex returns the same cached object on repeated access."""
        info = SpanInfo(trace_id=42)
        first = info.trace_id_hex
        second = info.trace_id_hex
        assert first is second

    def test_span_id_hex_cached(self):
        """span_id_hex returns the same cached object on repeated access."""
        info = SpanInfo(span_id=99)
        first = info.span_id_hex
        second = info.span_id_hex
        assert first is second

    def test_span_name_stored(self):
        """span_name property stores and returns the provided span name."""
        info = SpanInfo(span_name="my-span")
        assert info.span_name == "my-span"

class TestGetSpanInfo:
    def _make_span_context(self, trace_id: int, span_id: int) -> SpanContext:
        return SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=False,
            trace_flags=TraceFlags(0x01),
        )

    def test_uses_current_span_when_none_given(self):
        """Verify that uses current span when none given."""
        mock_span = MagicMock(spec=NonRecordingSpan)
        ctx = self._make_span_context(trace_id=100, span_id=200)
        mock_span.get_span_context.return_value = ctx
        mock_span.name = "test-span"

        with patch("aion.shared.opentelemetry.tracing.trace") as mock_trace:
            mock_trace.get_current_span.return_value = mock_span
            info = get_span_info()

        assert info.trace_id == 100
        assert info.span_id == 200

    def test_uses_provided_span(self):
        """Verify that uses provided span."""
        mock_span = MagicMock(spec=NonRecordingSpan)
        ctx = self._make_span_context(trace_id=0xABCD, span_id=0x1234)
        mock_span.get_span_context.return_value = ctx
        mock_span.name = "provided-span"

        info = get_span_info(span=mock_span)
        assert info.trace_id == 0xABCD
        assert info.span_id == 0x1234

    def test_zero_trace_id_stored_as_none(self):
        """Verify that zero trace ID stored as none."""
        mock_span = MagicMock()
        ctx = self._make_span_context(trace_id=0, span_id=0)
        mock_span.get_span_context.return_value = ctx

        info = get_span_info(span=mock_span)
        assert info.trace_id is None
        assert info.span_id is None

    def test_readable_span_populates_parent_span_id(self):
        """Verify that readable span populates parent span ID."""
        from opentelemetry.trace import SpanContext as SC

        parent_ctx = SC(
            trace_id=1,
            span_id=0x9999,
            is_remote=True,
            trace_flags=TraceFlags(0x01),
        )

        readable = MagicMock(spec=ReadableSpan)
        span_ctx = self._make_span_context(trace_id=1, span_id=0x1111)
        readable.get_span_context.return_value = span_ctx
        readable.parent = parent_ctx
        readable.name = "readable"

        info = get_span_info(span=readable)
        assert info.parent_span_id == 0x9999

    def test_readable_span_no_parent_leaves_none(self):
        """Verify that readable span no parent leaves none."""
        readable = MagicMock(spec=ReadableSpan)
        ctx = self._make_span_context(trace_id=1, span_id=2)
        readable.get_span_context.return_value = ctx
        readable.parent = None
        readable.name = "s"

        info = get_span_info(span=readable)
        assert info.parent_span_id is None

    def test_span_name_captured(self):
        """Verify that span name captured."""
        mock_span = MagicMock()
        ctx = self._make_span_context(trace_id=1, span_id=2)
        mock_span.get_span_context.return_value = ctx
        mock_span.name = "my-operation"

        info = get_span_info(span=mock_span)
        assert info.span_name == "my-operation"


class TestGenerateRequestSpanContext:
    def _patch_logger(self):
        # get_logger is imported lazily inside the function body, so we patch
        # where it lives, not where it's referenced from.
        return patch(
            "aion.shared.logging.factory.get_logger",
            return_value=MagicMock()
        )

    def test_returns_none_for_none_trace_id(self):
        """Verify that returns none for none trace ID."""
        with self._patch_logger():
            result = generate_request_span_context(trace_id=None)
        assert result is None

    def test_returns_context_for_int_trace_id(self):
        """Verify that returns context for int trace ID."""
        with self._patch_logger():
            ctx = generate_request_span_context(trace_id=0xABCDEF1234567890ABCDEF1234567890)
        assert ctx is not None

    def test_returns_context_for_hex_string_trace_id(self):
        """Verify that returns context for hex string trace ID."""
        hex_id = "abcdef1234567890abcdef1234567890"
        with self._patch_logger():
            ctx = generate_request_span_context(trace_id=hex_id)
        assert ctx is not None

    def test_returns_none_for_invalid_hex_trace_id(self):
        """Verify that returns none for invalid hex trace ID."""
        mock_logger = MagicMock()
        with patch("aion.shared.logging.factory.get_logger", return_value=mock_logger):
            ctx = generate_request_span_context(trace_id="not-valid-hex!!")
        assert ctx is None
        mock_logger.warning.assert_called_once()

    def test_none_span_id_uses_invalid_span_id(self):
        """Verify that none span ID uses invalid span ID."""
        with self._patch_logger():
            ctx = generate_request_span_context(trace_id=1, span_id=None)
        # Should not raise; INVALID_SPAN_ID is used internally
        assert ctx is not None

    def test_hex_string_span_id_converted(self):
        """Verify that hex string span ID converted."""
        hex_span = "0102030405060708"
        with self._patch_logger():
            ctx = generate_request_span_context(trace_id=1, span_id=hex_span)
        assert ctx is not None

    def test_invalid_hex_span_id_falls_back_to_invalid(self):
        """Verify that invalid hex span ID falls back to invalid."""
        mock_logger = MagicMock()
        with patch("aion.shared.logging.factory.get_logger", return_value=mock_logger):
            ctx = generate_request_span_context(trace_id=1, span_id="not-hex!!")
        # Falls back to INVALID_SPAN_ID; context still created (not None)
        assert ctx is not None
        mock_logger.warning.assert_called_once()

    def test_int_span_id_used_directly(self):
        """Verify that int span ID used directly."""
        with self._patch_logger():
            ctx = generate_request_span_context(trace_id=1, span_id=0x1234567890ABCDEF)
        assert ctx is not None

    def test_exception_in_span_context_returns_none(self):
        """Verify that exception in span context returns none."""
        mock_logger = MagicMock()
        with patch("aion.shared.logging.factory.get_logger", return_value=mock_logger):
            with patch(
                "aion.shared.opentelemetry.tracing.SpanContext",
                side_effect=RuntimeError("unexpected"),
            ):
                ctx = generate_request_span_context(trace_id=1)
        assert ctx is None
        mock_logger.exception.assert_called_once()

    def test_returned_context_can_be_used_with_tracer(self):
        """Integration-style: context returned by generate_request_span_context
        is a valid OTel Context object (has the span set)."""
        with self._patch_logger():
            ctx = generate_request_span_context(
                trace_id=0x0102030405060708090a0b0c0d0e0f10,
                span_id=0x0102030405060708,
            )
        # Should be usable with OTel API without errors
        span = otel_trace.get_current_span(ctx)
        assert span is not None


class TestInitTracing:
    def test_sets_tracer_provider(self):
        """Verify that sets tracer provider."""
        with patch("aion.shared.opentelemetry.tracing.trace") as mock_trace:
            init_tracing()
            mock_trace.set_tracer_provider.assert_called_once()
            # Argument should be a TracerProvider instance
            args = mock_trace.set_tracer_provider.call_args[0]
            assert isinstance(args[0], TracerProvider)
