__all__ = [
    "CHAT_COMPLETIONS_SUBSCRIPTION",
]

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
