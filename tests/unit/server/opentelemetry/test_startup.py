"""Startup attribution across concurrent tasks and thread work, without leaks."""

import asyncio
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aion.server.logging.filters import ServerAionContextFilter
from aion.server.opentelemetry.startup import (
    StartupSpan, startup_carrier, startup_phase, without_trace,
)


@pytest.fixture
def spans(monkeypatch):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield exporter
    provider.shutdown()


def _record(message):
    record = logging.LogRecord("startup.probe", logging.INFO, "", 0, message, (), None)
    ServerAionContextFilter().filter(record)
    return record


async def test_parallel_phases_and_migration_thread_keep_their_own_spans(spans):
    startup = StartupSpan("application.startup")
    records = []

    async def phase(name):
        with startup_phase(name):
            await asyncio.sleep(0)
            records.append(await asyncio.to_thread(_record, name))

    with startup.activate():
        await asyncio.gather(phase("database.prepare"), phase("agent.build"))
        parent = _record("parent")
    startup.finish()
    outside = _record("outside")

    assert {record.trace_id for record in records} == {parent.trace_id}
    assert {record.trace_span_name for record in records} == {"database.prepare", "agent.build"}
    assert len({record.trace_span_id for record in records}) == 2
    assert all(record.trace_parent_span_id == parent.trace_span_id for record in records)
    assert outside.trace_id is None
    assert len(spans.get_finished_spans()) == 3


def test_carrier_connects_a_child_and_a_later_launch_gets_a_fresh_trace(spans):
    root = StartupSpan("application.startup")
    with root.activate():
        child = StartupSpan("agent.startup", startup_carrier())
        # Missing context must not accidentally pick up an inherited span.
        independent = StartupSpan("agent.startup")
    assert child.span.parent.span_id == root.span.get_span_context().span_id
    assert child.span.get_span_context().trace_id == root.span.get_span_context().trace_id
    assert independent.span.get_span_context().trace_id != root.span.get_span_context().trace_id
    child.finish()
    root.finish()
    independent.finish()


async def test_background_tasks_do_not_retain_startup_context(spans):
    startup = StartupSpan("agent.startup")
    run = asyncio.Event()

    async def background():
        await run.wait()
        return _record("background")

    with startup.activate():
        with without_trace():
            task = asyncio.create_task(background())
        assert _record("restored").trace_id is not None
    startup.finish()
    run.set()
    assert (await task).trace_id is None


def test_failed_startup_keeps_error_status_and_ends_once(spans):
    startup = StartupSpan("agent.startup")
    startup.finish("failed", RuntimeError("database unavailable"))
    startup.finish("ready")
    completed, = spans.get_finished_spans()
    assert completed.status.status_code == trace.StatusCode.ERROR
    assert len(completed.events) == 1
    assert not trace.get_current_span().get_span_context().is_valid


def test_skipping_confirmation_does_not_claim_readiness(spans):
    startup = StartupSpan("application.startup")
    startup.finish("launched")
    completed, = spans.get_finished_spans()
    assert completed.status.status_code == trace.StatusCode.UNSET
