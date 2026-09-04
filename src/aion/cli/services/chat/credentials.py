"""Credential helper used by the Python-launched chat UI.

The standalone chat UI is implemented in Node. When users start it through the
Python SDK with ``aion chat``, the Python package should own Python-side runtime
concerns, including the dependency used to access the operating-system keychain.
The launcher advertises this module to the Node process through
``AION_CHAT_CREDENTIAL_HELPER`` as a JSON-encoded argv array.

The helper protocol is intentionally small: the Node process sends one JSON
request on stdin and receives one JSON response on stdout. Supported actions are
``get``, ``set``, and ``delete`` for the Aion WorkOS refresh token associated
with an environment. Python-launched chat stores credentials under a separate
service name from the npm ``aio``/``aion-chat`` keyring implementation, so the
two launch paths do not compete for ownership of the same keychain item.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

SERVICE_NAME = "aion-chat-python"


class CredentialHelperError(RuntimeError):
    """Error raised when the one-shot credential helper cannot complete."""


def _account_name(environment_id: str) -> str:
    """Return the Python helper account key for an environment.

    Args:
        environment_id: Aion environment identifier, such as ``development``.

    Returns:
        Account key used within the Python credential helper service namespace.
    """
    return f"{environment_id}:user"


def _load_keyring() -> Any:
    """Import the Python keyring dependency with a targeted error message.

    Returns:
        Imported ``keyring`` module.

    Raises:
        CredentialHelperError: If the Python package was installed without the
            runtime dependency needed to access the operating-system keychain.
    """
    try:
        import keyring
    except ImportError as exc:
        raise CredentialHelperError(
            "The Python keyring package is required for aion chat credentials."
        ) from exc
    return keyring


def _get_password(account_name: str) -> str | None:
    """Read a refresh token from the Python-launched chat credential namespace."""
    keyring = _load_keyring()
    return keyring.get_password(SERVICE_NAME, account_name)


def _set_password(account_name: str, password: str) -> None:
    """Store a refresh token in the Python-launched chat credential namespace."""
    keyring = _load_keyring()
    keyring.set_password(SERVICE_NAME, account_name, password)


def _delete_password(account_name: str) -> None:
    """Delete a refresh token from the Python-launched chat credential namespace."""
    keyring = _load_keyring()
    keyring.delete_password(SERVICE_NAME, account_name)


def _read_request(stdin: TextIO) -> dict[str, Any]:
    """Read and validate the JSON request sent by the chat UI.

    Args:
        stdin: Text stream containing the helper request JSON.

    Returns:
        Validated request object with an ``action`` and ``environmentId``.

    Raises:
        CredentialHelperError: If the request is not a JSON object, names an
            unsupported action, omits the environment, or omits the refresh token
            required for a ``set`` operation.
    """
    try:
        request = json.loads(stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        raise CredentialHelperError("Credential helper request was not valid JSON.") from exc

    if not isinstance(request, dict):
        raise CredentialHelperError("Credential helper request must be a JSON object.")

    action = request.get("action")
    environment_id = request.get("environmentId")
    if action not in {"get", "set", "delete"}:
        raise CredentialHelperError("Credential helper action must be get, set, or delete.")
    if not isinstance(environment_id, str) or not environment_id:
        raise CredentialHelperError("Credential helper environmentId must be a string.")
    if action == "set" and not isinstance(request.get("refreshToken"), str):
        raise CredentialHelperError("Credential helper set action requires refreshToken.")
    return request


def _handle_request(request: dict[str, Any]) -> dict[str, str | None]:
    """Execute a credential request against the Python keyring namespace.

    Args:
        request: Validated helper request from ``_read_request``.

    Returns:
        JSON-serializable response. ``get`` returns ``refreshToken`` when one is
        stored; ``set`` and ``delete`` return an empty object.

    Raises:
        CredentialHelperError: If the Python keyring dependency is unavailable.
        Exception: If the configured Python keyring backend fails.
    """
    action = request["action"]
    environment_id = request["environmentId"]
    account_name = _account_name(environment_id)

    if action == "get":
        token = _get_password(account_name)
        return {"refreshToken": token} if token else {}

    if action == "set":
        _set_password(account_name, request["refreshToken"])
        return {}

    _delete_password(account_name)
    return {}


def main() -> int:
    """Run the chat credential helper protocol once.

    Returns:
        Process exit code. Returns ``0`` after writing a JSON response to stdout
        and ``1`` after writing a helper error message to stderr.
    """
    try:
        response = _handle_request(_read_request(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - CLI helper must report all failures.
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
