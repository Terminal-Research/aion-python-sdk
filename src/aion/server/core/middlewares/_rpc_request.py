"""The fields of an A2A JSON-RPC request the server's middlewares read.

``CallerIdentityMiddleware`` and ``AionContextMiddleware`` both read a few
fields of the request body before a2a-sdk's dispatcher parses it: the method,
the id an error answers to, ``params.metadata`` and the distribution payload
in it. The first of them to ask prepares those fields and keeps the result
in the ASGI scope, which the middlewares and the endpoint share. The body is
decoded and the payload validated once among the middlewares, whichever of
them an application runs.

That is all "once" covers. For ``SendMessage`` the request handler's
extension pipeline validates the payload again for its preflight and the
runtime context, and a2a-sdk parses the body as the protocol. Preparing a
request reads only what the middlewares need and leaves the body as it came.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from a2a.server.jsonrpc_models import InvalidRequestError
from a2a.server.request_handlers import build_error_response
from a2a.utils import DEFAULT_RPC_URL
from aion.core.a2a.extensions.distribution import DistributionExtensionV1
from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.core.exceptions import AionError
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_SCOPE_KEY = "aion.prepared_rpc_request"


class InvalidExtensionPayloadError(AionError):
    """An extension payload in a request's metadata does not match its model.

    Holds only what is safe to log and to answer with: the id the answer goes
    to and a description of the problems, never the payload's values.
    """

    def __init__(self, request_id: str | int | None, detail: str) -> None:
        super().__init__(detail)
        self.request_id = request_id
        self.detail = detail


@dataclass(frozen=True)
class PreparedRpcRequest:
    """The fields of one JSON-RPC request that the middlewares read.

    method        — the method the body names, when it is a string.
    request_id    — the id an error answer goes to: the body's id when it is
                    a string or an integer, otherwise ``None``, as a2a-sdk's
                    dispatcher answers. The body keeps the id it came with.
    metadata      — ``params.metadata``, when it is an object.
    distribution  — the distribution payload in ``metadata``, validated;
                    ``None`` when the metadata carries none, JSON ``null``
                    included.

    ``metadata`` and ``distribution`` stay out of ``repr``: the payload's
    environment carries configuration variables, secrets among them.
    """

    method: Optional[str] = None
    request_id: str | int | None = None
    metadata: Optional[dict[str, Any]] = field(default=None, repr=False)
    distribution: Optional[DistributionExtensionV1] = field(default=None, repr=False)


def is_rpc_post(request: Request) -> bool:
    """Whether the request is a JSON-RPC call to the A2A endpoint."""
    return request.url.path == DEFAULT_RPC_URL and request.method == "POST"


async def prepare_rpc_request(request: Request) -> PreparedRpcRequest:
    """The request's prepared fields, prepared by whichever middleware asks first.

    Raises:
        InvalidExtensionPayloadError: The distribution payload is malformed.
            Nothing is kept, and the middleware that asked refuses the
            request with ``refuse_invalid_extension``.
    """
    prepared = request.scope.get(_SCOPE_KEY)
    if prepared is None:
        prepared = await _prepare(request)
        request.scope[_SCOPE_KEY] = prepared
    return prepared


def describe_validation_error(error: ValidationError) -> str:
    """Each problem's field path, message and error type - never its input.

    Pydantic's own rendering quotes the offending input, and an error's
    context can carry values as well. A distribution payload's environment
    holds secrets in plain text, so neither reaches a log or an answer.
    """
    problems = error.errors(include_url=False, include_context=False, include_input=False)
    described = "; ".join(
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']} [{problem['type']}]"
        for problem in problems
    )
    return f"{error.title}: {described}"


def refuse_invalid_extension(request_id: str | int | None, detail: str) -> JSONResponse:
    """Log and answer a request whose extension payload is malformed.

    The same invalid-request error with the same safe ``detail``, from
    whichever middleware found the problem.
    """
    logger.warning("Refused a request with an invalid extension payload in its metadata: %s", detail)
    return JSONResponse(
        build_error_response(request_id, InvalidRequestError(data=detail)),
        status_code=200,
    )


async def _prepare(request: Request) -> PreparedRpcRequest:
    try:
        body = await request.json()
    except Exception:
        return PreparedRpcRequest()
    if not isinstance(body, dict):
        return PreparedRpcRequest()

    method = body.get("method")
    request_id = body.get("id")
    if not isinstance(request_id, str | int):
        request_id = None
    params = body.get("params")
    metadata = params.get("metadata") if isinstance(params, dict) else None
    if not isinstance(metadata, dict):
        metadata = None

    try:
        distribution = _distribution(metadata)
    except ValidationError as error:
        # From None: the validation error quotes the payload it rejected.
        raise InvalidExtensionPayloadError(request_id, describe_validation_error(error)) from None

    return PreparedRpcRequest(
        method=method if isinstance(method, str) else None,
        request_id=request_id,
        metadata=metadata,
        distribution=distribution,
    )


def _distribution(metadata: Optional[dict[str, Any]]) -> Optional[DistributionExtensionV1]:
    """The distribution payload in the metadata, validated against its model.

    Required spec fields are enforced by the model, so a projection that
    omits one is refused rather than reaching the agent with silent gaps.
    """
    raw = (metadata or {}).get(DISTRIBUTION_EXTENSION_URI_V1)
    if raw is None:
        return None
    return DistributionExtensionV1.model_validate(raw)
