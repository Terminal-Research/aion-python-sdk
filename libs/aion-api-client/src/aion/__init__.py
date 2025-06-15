"""Namespace package for Aion SDK libraries.

This package exposes the :class:`ApiClient` and GraphQL data models for
consumers.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from .api_client import ApiClient, settings  # noqa: E402
from .gql.client import GqlClient  # noqa: E402
from .gql import generated  # noqa: E402

__all__ = ["ApiClient", "GqlClient", "settings", "generated"]
