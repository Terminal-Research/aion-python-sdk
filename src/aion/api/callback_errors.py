"""Preserve actionable callback configuration failures across HTTP adapters."""

from collections.abc import Mapping

import httpx

from aion.core.exceptions import AionDaemonIdentityRequired


def raise_callback_error(payload: object) -> None:
    """Raise only the stable missing-daemon code in known API error shapes.

    Args:
        payload: Decoded model/Files or JSON-RPC error envelope.
    """
    if not isinstance(payload, Mapping):
        return
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return
    details = error.get("data", error)
    if isinstance(details, Mapping) and details.get("code") == AionDaemonIdentityRequired.code:
        raise AionDaemonIdentityRequired(details.get("resourceType"), details.get("resourceId"))


def callback_response_hook(response: httpx.Response) -> None:
    """Inspect error bodies only; successful streams must remain unconsumed."""
    if response.is_error:
        response.read()
        _check_response(response)


async def async_callback_response_hook(response: httpx.Response) -> None:
    """Asynchronously inspect failures without buffering successful streams."""
    if response.is_error:
        await response.aread()
        _check_response(response)


def _check_response(response: httpx.Response) -> None:
    try:
        payload = response.json()
    except (ValueError, httpx.ResponseNotRead):
        return
    raise_callback_error(payload)


def reraise_callback_error(error: BaseException) -> None:
    """Recover the typed failure from provider responses or transport groups.

    Args:
        error: Framework wrapper; unrelated failures are left unchanged.
    """
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, AionDaemonIdentityRequired):
            raise current
        # OpenAI stream errors carry decoded details in .body, without an
        # HTTP failure response. The body may be an envelope or its error.
        body = getattr(current, "body", None)
        raise_callback_error(body)
        raise_callback_error({"error": body})
        response = getattr(current, "response", None)
        if isinstance(response, httpx.Response) and response.is_closed:
            _check_response(response)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif not current.__suppress_context__ and current.__context__ is not None:
            pending.append(current.__context__)
        # LiteLLM retains stream failures here when it wraps them for fallback.
        original = getattr(current, "original_exception", None)
        if isinstance(original, BaseException):
            pending.append(original)
