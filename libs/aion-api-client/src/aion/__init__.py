"""Namespace package for Aion SDK libraries.

This package exposes the :class:`ApiClient` and GraphQL data models for
consumers.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from .api import (
    AionGqlClient,
    AionHttpClient,
    generated
)  # noqa: E402

from aion.api.exceptions import (
    AionException,
    AionAuthenticationError,
)  # noqa: E402

__all__ = [
    "AionGqlClient",
    "AionHttpClient",
    "generated",

    # exceptions
    "AionException",
    "AionAuthenticationError",
]
