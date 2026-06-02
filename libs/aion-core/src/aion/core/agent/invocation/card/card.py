"""Card class for rich message rendering via the Distribution/Cards extension.

Three creation modes:

    Card(jsx="<Card>...</Card>")       # inline JSX — value object
    Card(url="https://...")            # remote document — value object
    Card("Deployment approved")        # builder — use .add() to compose
        .add(Text("..."))
        .add(Fields().add(Field("k", "v")))

See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
"""

from __future__ import annotations

from aion.core.agent.invocation.card.components import Component


class Card:
    """A provider-neutral card document for rich message rendering.

    Supports three creation modes:

    * **JSX value object** — wraps a pre-rendered JSX string::

        Card(jsx="<Card><Text>Hello</Text></Card>")

    * **URL value object** — points to a remote card document::

        Card(url="https://cdn.example.com/cards/deploy.jsx")

    * **Builder** — compose the card from typed components::

        Card("Deployment approved")
            .add(Text("Production rollout is ready."))
            .add(Fields().add(Field("Env", "prod")).add(Field("Run", "#42")))
            .add(Divider())
            .add(Actions().add(Button("Approve", id="approve", style="primary")))

    ``jsx`` and ``url`` are mutually exclusive; passing both raises
    :class:`ValueError`.  In builder mode, the final JSX is produced lazily
    by :attr:`jsx` when the card is serialized.
    """

    def __init__(
            self,
            title: str | None = None,
            *,
            jsx: str | None = None,
            url: str | None = None,
    ) -> None:
        """Initialize a Card.

        Args:
            title: Card heading rendered as the ``title`` attribute on the
                root ``<Card>`` element.  Only used in builder mode.
            jsx: Pre-rendered JSX string.  Mutually exclusive with *url*.
            url: URL of a remotely hosted card document.  Mutually exclusive
                with *jsx*.

        Raises:
            ValueError: If both *jsx* and *url* are provided, if *jsx* is an
                empty or whitespace-only string, or if *url* is empty or does
                not start with ``http://`` or ``https://``.
        """
        if jsx is not None and url is not None:
            raise ValueError("Card requires exactly one of jsx or url, got both.")
        if jsx is not None and not jsx.strip():
            raise ValueError("Card jsx must not be empty.")
        if url is not None:
            if not url.strip():
                raise ValueError("Card url must not be empty.")
            if not url.startswith(("http://", "https://")):
                raise ValueError("Card url must start with http:// or https://.")

        self._title = title
        self._jsx = jsx
        self._url = url
        self._children: list[Component] = []

    def add(self, child: Component) -> "Card":
        """Append a child component and return *self* for fluent chaining.

        Only valid in builder mode (i.e., the card was not created with
        ``jsx=`` or ``url=``).

        Args:
            child: Any :class:`~aion.core.agent.invocation.card.Component`
                instance — :class:`Text`, :class:`Fields`, :class:`Divider`,
                :class:`Actions`, etc.

        Returns:
            The same :class:`Card` instance to allow method chaining.

        Raises:
            ValueError: If the card was created with an explicit *jsx* or
                *url* value.
        """
        if self._jsx is not None or self._url is not None:
            raise ValueError("Cannot add children to a Card created with jsx or url.")
        self._children.append(child)
        return self

    @property
    def jsx(self) -> str | None:
        """The JSX representation of this card, or ``None`` for URL cards.

        * In JSX value-object mode, returns the string passed at construction.
        * In builder mode, renders children to JSX on first access.
        * In URL mode, always returns ``None``.
        """
        if self._jsx is not None:
            return self._jsx
        if self._url is not None:
            return None
        return self._render()

    @property
    def url(self) -> str | None:
        """The remote URL of this card, or ``None`` for JSX/builder cards."""
        return self._url

    def _render(self) -> str:
        """Render builder children to a ``<Card>`` JSX string."""
        attrs = f' title="{self._title}"' if self._title else ""
        body = "\n".join(c.to_jsx() for c in self._children)
        return f"<Card{attrs}>\n{body}\n</Card>"

    def __repr__(self) -> str:
        if self._url:
            return f"Card(url={self._url!r})"
        return f"Card(jsx={self.jsx!r})"
