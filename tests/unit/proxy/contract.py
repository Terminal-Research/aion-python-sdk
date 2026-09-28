"""What RequestHandler owes the proxy routes: forwarding fidelity and transport safety.

``RequestHandler`` is the core of the proxy: it receives an inbound request,
selects the target agent, forwards the request, and streams the response back
through ``UpstreamStreamingResponse``.  Its contract is the observable behavior
a caller — the route handler — relies on.

## The contract

The numbered guarantees are asserted, one test each, in
``test_proxy_handlers.py``.

 1. Upstream body chunks are relayed downstream as they arrive; the proxy does
    not buffer the complete response before forwarding.
 2. Hop-by-hop and framing response headers are stripped.  Headers dynamically
    named by the upstream ``connection`` header are also stripped.  End-to-end
    response headers pass through.
 3. Hop-by-hop and framing request headers are not forwarded upstream.
    End-to-end request headers pass through unchanged.
 4. The client's original ``content-length`` is not forwarded; httpx derives
    the correct value from the forwarded body.
 5. The HTTP method, request body, and path are forwarded to the target URL
    built from the agent's base URL.
 6. Query parameters from the incoming request are appended to the target URL.
 7. A request for an ``agent_id`` not in the ``agent_urls`` mapping raises
    ``AgentNotFoundException``.
 8. An ``httpx.ConnectError`` maps to ``AgentUnavailableException``.
 9. An ``httpx.TimeoutException`` maps to ``AgentTimeoutException``.
10. An upstream transport failure during response streaming ends the body
    silently rather than raising to the caller.
11. The upstream response stream is always closed after the downstream
    response completes, including when the downstream client disconnects.
"""
