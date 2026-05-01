from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class A2ARequestPreprocessor(Protocol):
    """Protocol for request preprocessors applied before handler routing.

    Preprocessors receive the parsed request object and may transform it
    in-place. They run after JSON validation, before the request is forwarded
    to the handler — covering both new-task and resume flows.

    Implement this protocol to add cross-cutting concerns such as file part
    transformation, content filtering, or request enrichment.
    """

    async def process(self, request_obj: Any) -> None:
        """Transform the request object in-place.

        Args:
            request_obj: Parsed A2A request (e.g. SendMessageRequest).
                         Only handle types relevant to this preprocessor;
                         ignore the rest.
        """
        ...

    async def rollback(self) -> None:
        """Undo side effects produced by the last preprocess() call.

        Called automatically when an error occurs after preprocess() has run.
        Preprocessors that produce side effects (e.g. file uploads) must
        implement this to clean up.
        """
        ...
