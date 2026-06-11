"""Card class and JSX components for rich message rendering via the Distribution/Cards extension.

Three creation modes:

    Card(jsx="<Card>...</Card>")       # inline JSX — value object
    Card(url="https://...")            # remote document — value object
    Card("Deployment approved")        # builder — use .add() to compose
        .add(Text("..."))
        .add(Fields().add(Field("k", "v")))

See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Component(ABC):
    """Abstract base class for all JSX Card components.

    Every component must implement :meth:`to_jsx`, which returns a
    self-contained JSX fragment string.  Components with children
    additionally expose an :meth:`add` method that returns *self* for
    fluent chaining.
    """

    @abstractmethod
    def to_jsx(self) -> str:
        """Render this component to a JSX fragment string."""
        ...


class Text(Component):
    """A prose block rendered as ``<Text>content</Text>``.

    Use for short descriptive sentences inside the card body.

    Example::

        Text("Production rollout is ready.")
    """

    def __init__(self, content: str) -> None:
        """Args:
            content: Plain-text content of the element.
        """
        self._content = content

    def to_jsx(self) -> str:
        return f"<Text>{self._content}</Text>"


class Field(Component):
    """A single labeled key-value pair rendered as ``<Field label="…">value</Field>``.

    Intended to be placed inside a :class:`Fields` group.

    Example::

        Field("Environment", "prod")
    """

    def __init__(self, label: str, value: str) -> None:
        """Args:
            label: Short descriptive label shown next to the value.
            value: The value to display.
        """
        self._label = label
        self._value = value

    def to_jsx(self) -> str:
        return f'<Field label="{self._label}">{self._value}</Field>'


class Fields(Component):
    """A group of :class:`Field` items rendered in a compact grid layout.

    Example::

        Fields()
            .add(Field("Env", "prod"))
            .add(Field("Run", "#42"))
    """

    def __init__(self) -> None:
        self._children: list[Field] = []

    def add(self, child: Field) -> "Fields":
        """Append a :class:`Field` and return *self* for chaining.

        Args:
            child: The :class:`Field` to append.

        Returns:
            This :class:`Fields` instance.
        """
        self._children.append(child)
        return self

    def to_jsx(self) -> str:
        body = "\n".join(c.to_jsx() for c in self._children)
        return f"<Fields>\n{body}\n</Fields>"


class Divider(Component):
    """A horizontal rule rendered as ``<Divider />``.

    Typically placed between the card body and the :class:`Actions` section.
    """

    def to_jsx(self) -> str:
        return "<Divider />"


class Button(Component):
    """An interactive button rendered as ``<Button …>label</Button>``.

    Two mutually exclusive modes:

    * **Callback** — pass ``id`` to trigger a server-side action::

        Button("Approve", id="approve", style="primary")

    * **Link** — pass ``url`` to open a URL in the browser::

        Button("Open run", url="https://example.com/run/42")

    Raises:
        ValueError: If neither *id* nor *url* is provided, or if both are.
    """

    def __init__(
        self,
        label: str,
        *,
        id: str | None = None,
        url: str | None = None,
        style: str | None = None,
    ) -> None:
        """Args:
            label: Visible text on the button.
            id: Callback identifier sent to the agent when the button is
                clicked.  Mutually exclusive with *url*.
            url: URL to open when the button is clicked.  Mutually exclusive
                with *id*.
            style: Optional visual style hint (e.g. ``"primary"``,
                ``"danger"``).

        Raises:
            ValueError: If neither *id* nor *url* is provided, or if both
                are provided at the same time.
        """
        if id is None and url is None:
            raise ValueError("Button requires either id or url.")
        if id is not None and url is not None:
            raise ValueError("Button requires exactly one of id or url, got both.")
        self._label = label
        self._id = id
        self._url = url
        self._style = style

    def to_jsx(self) -> str:
        attrs = ""
        if self._id:
            attrs += f' id="{self._id}"'
        if self._url:
            attrs += f' url="{self._url}"'
        if self._style:
            attrs += f' style="{self._style}"'
        return f"<Button{attrs}>{self._label}</Button>"


class Actions(Component):
    """A group of :class:`Button` controls rendered at the bottom of the card.

    Example::

        Actions()
            .add(Button("Approve", id="approve", style="primary"))
            .add(Button("Open run", url="https://example.com/run/42"))
    """

    def __init__(self) -> None:
        self._children: list[Button] = []

    def add(self, child: Button) -> "Actions":
        """Append a :class:`Button` and return *self* for chaining.

        Args:
            child: The :class:`Button` to append.

        Returns:
            This :class:`Actions` instance.
        """
        self._children.append(child)
        return self

    def to_jsx(self) -> str:
        body = "\n".join(c.to_jsx() for c in self._children)
        return f"<Actions>\n{body}\n</Actions>"


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
            child: Any :class:`Component` instance — :class:`Text`,
                :class:`Fields`, :class:`Divider`, :class:`Actions`, etc.

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
