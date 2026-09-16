"""Tests for Card class and card component builder."""

import pytest
from aion.core.agent.invocation.card import (
    Actions,
    Button,
    Card,
    Divider,
    Field,
    Fields,
    Text,
)


class TestCardValueObject:
    def test_jsx_mode(self):
        card = Card(jsx="<Card></Card>")
        assert card.jsx == "<Card></Card>"
        assert card.url is None

    def test_url_mode(self):
        card = Card(url="https://example.com/card.jsx")
        assert card.url == "https://example.com/card.jsx"
        assert card.jsx is None

    def test_both_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            Card(jsx="<Card></Card>", url="https://example.com")

    def test_jsx_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            Card(jsx="")

    def test_jsx_whitespace_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            Card(jsx="   ")

    def test_url_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            Card(url="")

    def test_url_no_scheme_raises(self):
        with pytest.raises(ValueError, match="http"):
            Card(url="example.com/card.jsx")

    def test_url_http_allowed(self):
        card = Card(url="http://example.com/card.jsx")
        assert card.url == "http://example.com/card.jsx"

    def test_empty_renders_empty_card(self):
        card = Card()
        assert card.jsx == "<Card>\n\n</Card>"
        assert card.url is None


class TestCardBuilder:
    def test_title_only(self):
        card = Card("Hello")
        assert card.jsx == '<Card title="Hello">\n\n</Card>'

    def test_add_text(self):
        card = Card("Title").add(Text("Body text"))
        assert "<Text>Body text</Text>" in card.jsx
        assert 'title="Title"' in card.jsx

    def test_add_fields(self):
        card = Card("Title").add(
            Fields()
            .add(Field("Env", "prod"))
            .add(Field("Run", "#42"))
        )
        jsx = card.jsx
        assert '<Field label="Env">prod</Field>' in jsx
        assert '<Field label="Run">#42</Field>' in jsx

    def test_add_divider(self):
        card = Card("Title").add(Divider())
        assert "<Divider />" in card.jsx

    def test_add_actions(self):
        card = Card("Title").add(
            Actions()
            .add(Button("Approve", id="approve", style="primary"))
            .add(Button("Open", url="https://example.com"))
        )
        jsx = card.jsx
        assert 'id="approve"' in jsx
        assert 'style="primary"' in jsx
        assert 'url="https://example.com"' in jsx

    def test_full_card(self):
        card = (
            Card("Deployment approved")
            .add(Text("Production rollout is ready."))
            .add(
                Fields()
                .add(Field("Environment", "prod"))
                .add(Field("Run", "#42"))
            )
            .add(Divider())
            .add(
                Actions()
                .add(Button("Approve", id="approve", style="primary"))
                .add(Button("Open run", url="https://example.com/run/42"))
            )
        )
        jsx = card.jsx
        assert 'title="Deployment approved"' in jsx
        assert "<Text>Production rollout is ready.</Text>" in jsx
        assert "<Divider />" in jsx
        assert 'id="approve"' in jsx

    def test_add_to_jsx_card_raises(self):
        with pytest.raises(ValueError, match="Cannot add children"):
            Card(jsx="<Card></Card>").add(Text("x"))

    def test_add_to_url_card_raises(self):
        with pytest.raises(ValueError, match="Cannot add children"):
            Card(url="https://example.com").add(Text("x"))

    def test_chaining_returns_self(self):
        card = Card("Title")
        result = card.add(Text("x"))
        assert result is card


class TestComponents:
    def test_text(self):
        assert Text("hello").to_jsx() == "<Text>hello</Text>"

    def test_field(self):
        assert Field("Env", "prod").to_jsx() == '<Field label="Env">prod</Field>'

    def test_divider(self):
        assert Divider().to_jsx() == "<Divider />"

    def test_button_id(self):
        jsx = Button("Approve", id="approve", style="primary").to_jsx()
        assert 'id="approve"' in jsx
        assert 'style="primary"' in jsx
        assert ">Approve<" in jsx

    def test_button_url(self):
        jsx = Button("Open", url="https://example.com").to_jsx()
        assert 'url="https://example.com"' in jsx

    def test_button_requires_id_or_url(self):
        with pytest.raises(ValueError, match="requires either"):
            Button("label")

    def test_button_both_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            Button("label", id="x", url="https://example.com")
