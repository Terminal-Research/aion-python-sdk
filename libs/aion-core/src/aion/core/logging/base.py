from __future__ import annotations

import logging
from typing import Optional


class AionLogRecord(logging.LogRecord):
    """
    Custom LogRecord for Aion logging.

    Declares all context attributes — initialized to None.
    Attributes are populated by ServerAionContextFilter (aion-server) before
    records reach any handler.

    Attributes:
        # OpenTelemetry tracing
        trace_id (Optional[str]): Active trace ID in hex format.
        trace_span_id (Optional[str]): Current span ID in hex format.
        trace_span_name (Optional[str]): Current span name.
        trace_parent_span_id (Optional[str]): Parent span ID in hex format.
        trace_baggage (Optional[dict]): W3C baggage from trace context.

        # Request / transaction context
        transaction_id (Optional[str]): Unique identifier of the current transaction.
        transaction_name (Optional[str]): Human-readable transaction name.
        http_request_method (Optional[str]): HTTP method of the incoming request.
        http_request_target (Optional[str]): HTTP request path / target URI.

        # Aion deployment
        aion_distribution_id (Optional[str]): Agent distribution identifier.
        aion_version_id (Optional[str]): Agent version identifier.
        aion_agent_environment_id (Optional[str]): Agent environment identifier.

        # A2A protocol
        task_id (Optional[str]): A2A task identifier.
        a2a_rpc_method (Optional[str]): JSON-RPC method name of the A2A call.
        a2a_task_status (Optional[str]): Current state of the A2A task.

        # Agent framework
        agent_trace_baggage (Optional[dict]): Snapshot of agent framework baggage.
    """

    trace_id: Optional[str]
    trace_span_id: Optional[str]
    trace_span_name: Optional[str]
    trace_parent_span_id: Optional[str]
    trace_baggage: Optional[dict]
    transaction_id: Optional[str]
    transaction_name: Optional[str]
    http_request_method: Optional[str]
    http_request_target: Optional[str]
    aion_distribution_id: Optional[str]
    aion_version_id: Optional[str]
    aion_agent_environment_id: Optional[str]
    task_id: Optional[str]
    a2a_rpc_method: Optional[str]
    a2a_task_status: Optional[str]
    agent_trace_baggage: Optional[dict]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace_id = None
        self.trace_span_id = None
        self.trace_span_name = None
        self.trace_parent_span_id = None
        self.trace_baggage = None
        self.transaction_id = None
        self.transaction_name = None
        self.http_request_method = None
        self.http_request_target = None
        self.aion_distribution_id = None
        self.aion_version_id = None
        self.aion_agent_environment_id = None
        self.task_id = None
        self.a2a_rpc_method = None
        self.a2a_task_status = None
        self.agent_trace_baggage = None


class AionLogger(logging.Logger):
    """Custom Logger that creates AionLogRecord instances."""

    def makeRecord(
            self,
            name,
            level,
            fn,
            lno,
            msg,
            args,
            exc_info,
            func=None,
            extra=None,
            sinfo=None,
    ) -> AionLogRecord:
        rv = AionLogRecord(name, level, fn, lno, msg, args, exc_info, func, sinfo)
        if extra is not None:
            for key in extra:
                if (key in ["message", "asctime"]) or (key in rv.__dict__):
                    raise KeyError("Attempt to overwrite %r in AionLogRecord" % key)
                rv.__dict__[key] = extra[key]
        return rv
