"""Typed calls to Aion's experimental Decisions API using an OpenAI client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, cast
from urllib.parse import urljoin

from pydantic import JsonValue

from .callback_errors import reraise_callback_error
from .model_service_client import aion_model_request_headers

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI


class DecisionQuestion(TypedDict):
    """A named question; Aion validates criteria against its type and model."""

    type: Literal["noul", "choice", "score", "box"]
    instructions: JsonValue
    criteria: NotRequired[JsonValue]


class DecisionRequest(TypedDict):
    """Transient evidence and named questions, with optional routing metadata."""

    model: str
    state: str | list[JsonValue] | dict[str, JsonValue]
    questions: dict[str, DecisionQuestion]
    provider: NotRequired[dict[str, JsonValue] | None]
    session_id: NotRequired[str]
    trace: NotRequired[dict[str, JsonValue]]
    user: NotRequired[str]


class _AnswerMetadata(TypedDict, total=False):
    confidence: float
    probabilities: dict[str, float] | None


class NoulAnswer(_AnswerMetadata):
    """Probability of an affirmative answer, between zero and one."""

    type: Literal["noul"]
    noul: float


class ChoiceAnswer(_AnswerMetadata):
    """Selected criterion key with optional confidence and probabilities."""

    type: Literal["choice"]
    choice: str


class ScoreAnswer(_AnswerMetadata):
    """A possibly fractional score on the requested criterion scale."""

    type: Literal["score"]
    score: float
    legend: NotRequired[dict[str, JsonValue]]


class DecisionBox(TypedDict):
    """Box edges expressed as fractions of the image dimensions."""

    top: float
    bottom: float
    left: float
    right: float


class BoxAnswer(_AnswerMetadata):
    """A detected box; absence detection requires a separate Noul question."""

    type: Literal["box"]
    box: DecisionBox


DecisionAnswer = NoulAnswer | ChoiceAnswer | ScoreAnswer | BoxAnswer
"""Discriminated answer variants keyed by the question's original name."""


class DecisionUsage(TypedDict, total=False):
    """Observed counts and Aion's response-time credit quote, when available."""

    input_tokens: int | None
    output_tokens: int | None
    cost: float


class DecisionResponse(TypedDict):
    """Named answers and optional usage values; missing cost does not mean free."""

    id: NotRequired[str]
    model: str
    answers: dict[str, DecisionAnswer]
    usage: DecisionUsage


def create_decision(client: OpenAI, request: DecisionRequest) -> DecisionResponse:
    """Evaluate questions with a caller-owned, Aion-configured OpenAI client.

    The helper selects the alpha endpoint on the configured host and attaches
    current request attribution. It disables SDK retries for this call because
    Aion already owns the paid-attempt retry policy. The client's credentials,
    timeout and HTTP transport remain in use; its lifecycle stays with the caller.

    Args:
        client: OpenAI client configured with ``aion_openai_config()``.
        request: Model, transient state, named questions and supported options.

    Returns:
        Aion's validated answer object, with static types for each answer kind.

    Raises:
        AionDaemonIdentityRequired: The deployment needs a configured daemon.
        APIError: The OpenAI transport's error for other HTTP or network failures.
    """
    try:
        result = client.with_options(max_retries=0).post(
            _endpoint(str(client.base_url)),
            cast_to=dict[str, Any],
            body=request,
            options={"headers": aion_model_request_headers()},
        )
    except Exception as error:
        reraise_callback_error(error)
        raise
    return cast(DecisionResponse, result)


async def create_decision_async(
    client: AsyncOpenAI, request: DecisionRequest,
) -> DecisionResponse:
    """Asynchronously evaluate questions without blocking an agent event loop.

    Args:
        client: Caller-owned AsyncOpenAI client targeting Aion's model base URL,
            with a refreshing asynchronous API-key provider.
        request: The same typed request accepted by ``create_decision``.

    Returns:
        Named answers and observed usage. Transport behavior and errors match
        ``create_decision``; cancellation propagates without retrying the call.
    """
    try:
        result = await client.with_options(max_retries=0).post(
            _endpoint(str(client.base_url)),
            cast_to=dict[str, Any],
            body=request,
            options={"headers": aion_model_request_headers()},
        )
    except Exception as error:
        reraise_callback_error(error)
        raise
    return cast(DecisionResponse, result)


def _endpoint(base_url: str) -> str:
    """Keep the configured origin and mount prefix, replacing only ``v1``."""
    return urljoin(base_url.rstrip("/") + "/", "../alpha/decisions")
