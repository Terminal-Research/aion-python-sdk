"""Authentication errors raised while talking to the Aion control plane.

The classes themselves live with the rest of the SDK's public hierarchy in
:mod:`aion.core.exceptions`; this module re-exports them, so the import path
callers already use keeps working.
"""

from aion.core.exceptions import (
    AionAuthenticationError,
    AionError,
    AionModelPrincipalError,
)

__all__ = [
    "AionError",
    "AionAuthenticationError",
    "AionModelPrincipalError",
]
