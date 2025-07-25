"""
Aion API GraphQL clients.

DEPRECATION NOTICE:
==================
The clients in this module (GqlClient and AionGqlApiClient) are deprecated
and will be removed.

These legacy clients were built using manual GraphQL query construction and
the gql library. They have been replaced by auto-generated clients using
ariadne-codegen, which provides:

- Type-safe GraphQL operations
- Automatic schema validation
- Better IDE support with autocompletion
- Reduced maintenance overhead
- Consistent API patterns

For new code, please use the generated clients from ariadne-codegen instead.

Legacy exports (deprecated):
- GqlClient: Low-level WebSocket GraphQL client
- AionGqlApiClient: High-level programmatic API interface
"""
from .client import GqlClient
from .api_client import AionGqlApiClient

__all__ = [
    "GqlClient",
    "AionGqlApiClient",
]