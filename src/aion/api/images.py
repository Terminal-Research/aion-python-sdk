"""Image generation with Aion credentials and request-local attribution."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import aclosing, asynccontextmanager, closing, contextmanager, nullcontext
from typing import Any

import httpx

from aion.api.callback_errors import (
    async_callback_response_hook,
    callback_response_hook,
    raise_callback_error,
)
from aion.api.model_service_client import (
    aion_jwt_api_key,
    aion_model_api_key,
    aion_model_base_url,
    aion_model_request_headers,
)
from aion.core.exceptions import AionImageError


def generate_images(
    model: str,
    prompt: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 480.0,
    **options: Any,
) -> dict[str, Any]:
    """Generate images and return the complete JSON response.

    Credentials and callback attribution are resolved for this request. Without
    a runtime request, Aion resolves the deployment's configured daemon. An
    active request with missing attribution fails rather than changing identity.

    Args:
        model: Image model ID from the Aion catalog.
        prompt: Instructions for the image generator.
        client: Optional caller-owned HTTPX client for connection reuse.
        timeout: Seconds allowed for each network operation; connect is capped
            at ten seconds. This is not a whole-request deadline.
        **options: Supported image controls, such as n, size, and quality.
            Use stream_images instead of supplying the stream option.

    Returns:
        The response object, including created, data, and optional usage.
        Each data item contains b64_json. usage.cost, if present, is in Aion
        credits. The complete JSON response is held in client memory.

    Raises:
        AionAuthenticationError: Credentials or request attribution are invalid.
        AionDaemonIdentityRequired: Self-initiated work needs a daemon identity.
        AionImageError: The response is not a valid image result.
        httpx.HTTPError: HTTP status, transport, or timeout failure. The helper
            does not retry generation automatically.
    """
    with _open(model, prompt, False, client, timeout, options) as response:
        _require_format(response, "application/json")
        response.read()
        return _result(response)


async def generate_images_async(
    model: str,
    prompt: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 480.0,
    **options: Any,
) -> dict[str, Any]:
    """Generate images without blocking an agent's event loop.

    Args:
        model: Image model ID from the Aion catalog.
        prompt: Instructions for the image generator.
        client: Optional caller-owned asynchronous HTTPX client.
        timeout: Per-network-operation timeout in seconds, as in generate_images.
        **options: Supported image controls; do not supply stream.

    Returns:
        The same complete JSON object as generate_images. Credentials refresh
        asynchronously; cancellation closes the response without retrying.
    """
    async with _open_async(model, prompt, False, client, timeout, options) as response:
        _require_format(response, "application/json")
        await response.aread()
        return _result(response)


@contextmanager
def stream_images(
    model: str,
    prompt: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 480.0,
    **options: Any,
) -> Iterator[Iterator[dict[str, Any]]]:
    """Open a context-managed iterator of image JSON events.

    Args:
        model: Image model ID from the Aion catalog.
        prompt: Instructions for the image generator.
        client: Optional caller-owned HTTPX client.
        timeout: Per-network-operation timeout in seconds, as in generate_images.
        **options: Supported image controls; stream is set to true by the helper.

    Yields:
        An iterator of partial_image and completed event dictionaries. Read it
        inside the with block. A JSON fallback becomes completed events, with
        the whole-response usage attached only to the last image. Native SSE
        usage is preserved as received. Each event is buffered in memory;
        this iterator does not expose binary image chunks or save files.

    Raises:
        AionImageError: A native error, malformed result, or missing SSE terminal
            marker. Completed images yielded earlier remain available to the
            caller. Leaving the context early closes the stream intentionally.
        httpx.HTTPError: HTTP status, transport, or timeout failure.
    """
    with _open(model, prompt, True, client, timeout, options) as response:
        with closing(_events(response)) as events:
            yield events


@asynccontextmanager
async def stream_images_async(
    model: str,
    prompt: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 480.0,
    **options: Any,
) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
    """Open the asynchronous equivalent of stream_images.

    Args:
        model: Image model ID from the Aion catalog.
        prompt: Instructions for the image generator.
        client: Optional caller-owned asynchronous HTTPX client.
        timeout: Per-network-operation timeout in seconds, as in generate_images.
        **options: Supported image controls; do not supply stream.

    Yields:
        An asynchronous event iterator for use with async for inside async with.
        Fallback, usage, errors, and memory behavior match stream_images. Early
        exit or cancellation closes the response, not a caller-owned client.
    """
    async with _open_async(model, prompt, True, client, timeout, options) as response:
        async with aclosing(_events_async(response)) as events:
            yield events


def _request(model: str, prompt: str, stream: bool, options: Mapping[str, Any]) -> dict[str, Any]:
    if "stream" in options:
        raise AionImageError("Select generate_images or stream_images instead of passing stream.")
    return {**options, "model": model, "prompt": prompt, "stream": stream}


@contextmanager
def _open(
    model: str, prompt: str, stream: bool, client: httpx.Client | None,
    timeout: float, options: Mapping[str, Any],
) -> Iterator[httpx.Response]:
    body = _request(model, prompt, stream, options)
    with (nullcontext(client) if client is not None else httpx.Client()) as http:
        headers = httpx.Headers(aion_model_request_headers(http.headers))
        headers["Authorization"] = f"Bearer {aion_model_api_key()}"
        headers["Accept"] = "application/json, text/event-stream"
        with http.stream(
            "POST", f"{aion_model_base_url()}/images", json=body, headers=headers, auth=None,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
        ) as response:
            callback_response_hook(response)
            response.raise_for_status()
            yield response


@asynccontextmanager
async def _open_async(
    model: str, prompt: str, stream: bool, client: httpx.AsyncClient | None,
    timeout: float, options: Mapping[str, Any],
) -> AsyncIterator[httpx.Response]:
    body = _request(model, prompt, stream, options)
    async with (nullcontext(client) if client is not None else httpx.AsyncClient()) as http:
        headers = httpx.Headers(aion_model_request_headers(http.headers))
        headers["Authorization"] = f"Bearer {await aion_jwt_api_key()}"
        headers["Accept"] = "application/json, text/event-stream"
        async with http.stream(
            "POST", f"{aion_model_base_url()}/images", json=body, headers=headers, auth=None,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
        ) as response:
            await async_callback_response_hook(response)
            response.raise_for_status()
            yield response


def _format(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _require_format(response: httpx.Response, expected: str) -> None:
    if _format(response) != expected:
        raise AionImageError("Unexpected image response Content-Type.")


def _json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except ValueError:
        raise AionImageError("Invalid image response JSON.") from None
    if not isinstance(parsed, dict):
        raise AionImageError("Expected an image response object.")
    return parsed


def _image(value: Any) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("b64_json"), str) or not value["b64_json"]:
        raise AionImageError("Image response has no base64 image data.")


def _result(response: httpx.Response) -> dict[str, Any]:
    result = _json(response.text)
    images = result.get("data")
    if not isinstance(images, list) or not images:
        raise AionImageError("Image response contains no completed images.")
    for image in images:
        _image(image)
    return result


def _fallback(result: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Do not duplicate a whole-response price onto every completed image."""
    for index, image in enumerate(result["data"]):
        event = {**image, "type": "image_generation.completed"}
        if "created" in result:
            event["created"] = result["created"]
        if index == len(result["data"]) - 1 and "usage" in result:
            event["usage"] = result["usage"]
        yield event


def _events(response: httpx.Response) -> Iterator[dict[str, Any]]:
    if _format(response) == "application/json":
        response.read()
        yield from _fallback(_result(response))
        return
    _require_format(response, "text/event-stream")
    decoder = _ImageEvents()
    for line in response.iter_lines():
        event = decoder.feed(line)
        if event is not None:
            yield event
        if decoder.done:
            return
    raise AionImageError("Image stream ended before [DONE].")


async def _events_async(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    if _format(response) == "application/json":
        await response.aread()
        for event in _fallback(_result(response)):
            yield event
        return
    _require_format(response, "text/event-stream")
    decoder = _ImageEvents()
    async for line in response.aiter_lines():
        event = decoder.feed(line)
        if event is not None:
            yield event
        if decoder.done:
            return
    raise AionImageError("Image stream ended before [DONE].")


class _ImageEvents:
    """Share SSE framing and terminal validation across sync/async transports."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.completed = 0
        self.done = False

    def feed(self, line: str) -> dict[str, Any] | None:
        if line:
            field, separator, value = line.partition(":")
            if field == "data":
                self.lines.append(value.removeprefix(" ") if separator else "")
            return None
        if not self.lines:
            return None
        payload = "\n".join(self.lines)
        self.lines.clear()
        if payload == "[DONE]":
            if not self.completed:
                raise AionImageError("Image stream ended without a completed image.")
            self.done = True
            return None
        event = _json(payload)
        kind = event.get("type")
        if kind == "error":
            raise_callback_error(event)
            raise AionImageError("Image generation failed during streaming.")
        if kind not in ("image_generation.partial_image", "image_generation.completed"):
            raise AionImageError("Unknown image stream event type.")
        _image(event)
        if kind == "image_generation.completed":
            self.completed += 1
        return event
