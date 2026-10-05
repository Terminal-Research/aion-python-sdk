"""The Context extension: GetContexts, GetContext and DeleteContext.

See: https://docs.aion.to/a2a/extensions/aion/context/1.0.0
"""

from .errors import ContextDeletionInProgress, ContextLifecycleError, ContextNotDeletable, ContextNotFound
from .service import CANCEL_GROUP_SIZE, ContextService, ContextStateDeleter

__all__ = [
    "CANCEL_GROUP_SIZE",
    "ContextDeletionInProgress",
    "ContextLifecycleError",
    "ContextNotDeletable",
    "ContextNotFound",
    "ContextService",
    "ContextStateDeleter",
]
