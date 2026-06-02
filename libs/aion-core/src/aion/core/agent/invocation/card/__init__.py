"""Card builder for rich message rendering via the Distribution/Cards extension.

Provides the :class:`Card` value object / builder and all composable
JSX components.  Agent developers import from this package:

    from aion.core.agent.invocation.card import Card, Text, Fields, Field, Actions, Button, Divider

See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
"""

from .card import Card
from .components import Actions, Button, Component, Divider, Field, Fields, Text

__all__ = [
    "Card",
    "Component",
    "Text",
    "Field",
    "Fields",
    "Divider",
    "Button",
    "Actions",
]
