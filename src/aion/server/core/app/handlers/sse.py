"""How the server frames the events of an SSE response."""

SSE_LINE_SEPARATOR = '\n'
"""The line separator of every SSE response, in place of sse-starlette's CRLF.

LF is a valid SSE line separator. Through ``aion.proxy`` and the HTTP tunnel of
a remote deployment, CRLF event boundaries mixed with HTTP/1.1 chunk framing:
consecutive events arrived joined, and the client read invalid JSON. With LF
the blank line between events does not depend on the transfer framing.
"""
