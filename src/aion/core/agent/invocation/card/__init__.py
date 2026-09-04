"""Card builder for rich message rendering via the Distribution/Cards extension.

Provides the :class:`Card` value object / builder and all composable
JSX components.  Agent developers import from this package:

    from aion.core.agent.invocation.card import Card, Text, Fields, Field, Actions, Button, Divider

See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
"""

from .card import Actions, Button, Card, Component, Divider, Field, Fields, Text
from .utils import build_card_a2a_part, extract_card_name, is_jsx_card

__all__ = [
    "Card",
    "Component",
    "Text",
    "Field",
    "Fields",
    "Divider",
    "Button",
    "Actions",
    "is_jsx_card",
    "extract_card_name",
    "build_card_a2a_part",
]
