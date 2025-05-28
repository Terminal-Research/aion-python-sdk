from typing import Any

from starlette.responses import Response, StreamingResponse
from starlette.types import Send

"""
Patch Response.render and StreamingResponse.stream_response
to recognize bytearrays and memoryviews as bytes-like objects.
"""


def Response_render(self, content: Any) -> bytes:
    if content is None:
        return b""
    if isinstance(content, (bytes, bytearray, memoryview)):  # noqa: UP038
        return content
    return content.encode(self.charset)  # type: ignore


async def StreamingResponse_stream_response(self, send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": self.status_code,
            "headers": self.raw_headers,
        }
    )
    async for chunk in self.body_iterator:
        if chunk is None:
            continue
        if not isinstance(chunk, (bytes, bytearray, memoryview)):  # noqa: UP038
            chunk = chunk.encode(self.charset)
        await send({"type": "http.response.body", "body": chunk, "more_body": True})

    await send({"type": "http.response.body", "body": b"", "more_body": False})


# patch StreamingResponse.stream_response

StreamingResponse.stream_response = StreamingResponse_stream_response

# patch Response.render

Response.render = Response_render
