"""High level programmatic interface for the Aion API."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Iterable, Optional

from ..gql.client import GqlClient

CHAT_COMPLETIONS_SUBSCRIPTION = """
subscription ChatCompletions($request: ChatCompletionRequest!) {
  chatCompletionStream(request: $request) {
    ... on ChatCompletionStreamResponseChunk {
      response {
        id
        created
        model
        choices {
          index
          delta {
            role
            content
          }
          finishReason
        }
      }
    }
    ... on ChatCompletionStreamError {
      message
    }
    ... on ChatCompletionStreamComplete {
      done
    }
  }
}
"""


class ApiClient:
    """Programmatic interface exposing Aion API functionality."""

    def __init__(self, gql_client: Optional[GqlClient] = None) -> None:
        self._gql = gql_client or GqlClient()

    async def chat_completions(
        self,
        model: str,
        messages: Iterable[dict[str, str]],
        *,
        stream: bool = True,
    ) -> AsyncGenerator[Any, None]:
        """Request a chat completion stream.

        Args:
            model: Identifier of the model to use.
            messages: Sequence of message dictionaries with ``role`` and ``content``.
            stream: Whether to request a streaming response.

        Yields:
            GraphQL response chunks from the ``chatCompletionStream`` subscription.
        """

        variables = {"request": {"model": model, "messages": list(messages), "stream": stream}}
        async for chunk in self._gql.subscribe(CHAT_COMPLETIONS_SUBSCRIPTION, variables=variables):
            yield chunk["chatCompletionStream"]
