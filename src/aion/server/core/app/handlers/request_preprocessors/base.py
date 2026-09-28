"""Protocol and context for pluggable A2A request preprocessors."""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from a2a.server.context import ServerCallContext
from aion.core.runtime.context import AionRuntimeExtensions


@dataclass(frozen=True)
class PreprocessingContext:
    """What a preprocessor is allowed to act on, and what proves it.

    ``extensions`` is the verified result of the same collect/verify pipeline
    the executor runs later, not the raw declaration off the wire. A
    preprocessor with an external side effect - storing a file into the
    organization the client named, say - must act on this rather than on
    anything parsed earlier: metadata that is well-formed has not yet been
    accepted, and acting on it would let a client trigger the side effect with
    a declaration the agent is about to reject.

    The runtime context does not exist yet at this point in the request; this
    projection is what stands in for it.
    """

    extensions: AionRuntimeExtensions
    call_context: ServerCallContext


@runtime_checkable
class A2ARequestPreprocessor(Protocol):
    """Protocol for request preprocessors applied before request handling.

    Preprocessors receive the parsed request object and may transform it
    in-place. They run after extension verification and before the request
    reaches the handler - covering both new-task and resume flows.

    Implement this protocol to add cross-cutting concerns such as file part
    transformation, content filtering, or request enrichment.
    """

    async def process(self, request_obj: Any, context: PreprocessingContext) -> None:
        """Transform the request object in-place.

        Args:
            request_obj: Parsed A2A request (e.g. SendMessageRequest).
                         Only handle types relevant to this preprocessor;
                         ignore the rest.
            context: Verified projection of the request being preprocessed.

        Raises:
            A2AError: To reject the request. A preprocessor that raises after
                producing side effects must leave them compensable - its own
                ``rollback`` is called afterwards.
        """
        ...

    async def rollback(self) -> None:
        """Undo side effects produced by the last ``process`` call.

        Called when the request fails afterwards, and when ``process`` itself
        raises - including for the preprocessor that raised, whose batch may be
        half-done. Must be safe to call when nothing was produced.
        """
        ...
