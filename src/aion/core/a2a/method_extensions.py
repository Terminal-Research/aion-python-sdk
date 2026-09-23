"""JSON-RPC bindings for the Aion A2A method extensions.

An A2A extension is not one kind of thing. The specification uses *extension*
as the umbrella term and distinguishes, among others, message extensions,
which augment or alter the data of a standard request, and **method
extensions**, which add an RPC method of their own while remaining ordinary
extensions with their own URI. ``GetContext`` and ``GetContexts`` are method
extensions.

That is a statement about extension points, not about layering, so it does not
give these two a registry of their own. Their identity, activation and
exposure policy stay in ``AionA2AExtensionRegistry`` with every other
extension; what is specific to them is that one transport - JSON-RPC - has to
know which method name carries which extension, which params model parses it,
and which handler answers it. That is what a binding is, and it is the whole
of the dispatcher's routing table rather than a description of one:

    extension registry  ->  identity, active, available, advertised
    JSON-RPC binding    ->  method name, params model, handler, extension URI
    task handler        ->  ownership of task execution

Nothing here says whether an extension is enabled or published. A binding that
carried its own ``advertised`` or ``internal`` flag could disagree with the
descriptor; having no such field, it cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Type

from aion.core.constants.a2a import (
    GET_CONTEXT_EXTENSION_URI_V1,
    GET_CONTEXTS_LIST_EXTENSION_URI_V1,
)

from .request_params import GetContextParams, GetContextsListParams

__all__ = [
    "AionJsonRpcMethodExtensionBinding",
    "AION_JSONRPC_METHOD_EXTENSION_BINDINGS",
]


@dataclass(frozen=True)
class AionJsonRpcMethodExtensionBinding:
    """How one JSON-RPC method reaches the extension that defines it.

    Attributes:
        extension_uri: The extension this method belongs to, as registered in
            AionA2AExtensionRegistry. Load-bearing rather than documentary:
            the dispatcher refuses to start when it names a URI no descriptor
            claims, and carries it on the call context of every request it
            routes, so a method cannot drift away from the identity that
            governs its exposure.
        params_model: Model the method's `params` object is validated
            against. The dispatcher reads it from here, so there is one
            answer to "what does this method take" rather than two that can
            disagree.
        handler_name: Method on the request handler that answers this call.
            Named here for the same reason as the model: a binding that said
            which method exists but not who answers it would leave the real
            routing somewhere else, free to disagree with it.
    """

    extension_uri: str
    params_model: Type
    handler_name: str


AION_JSONRPC_METHOD_EXTENSION_BINDINGS: Mapping[str, AionJsonRpcMethodExtensionBinding] = (
    MappingProxyType(
        {
            "GetContext": AionJsonRpcMethodExtensionBinding(
                extension_uri=GET_CONTEXT_EXTENSION_URI_V1,
                params_model=GetContextParams,
                handler_name="on_get_context",
            ),
            "GetContexts": AionJsonRpcMethodExtensionBinding(
                extension_uri=GET_CONTEXTS_LIST_EXTENSION_URI_V1,
                params_model=GetContextsListParams,
                handler_name="on_get_contexts_list",
            ),
        }
    )
)
