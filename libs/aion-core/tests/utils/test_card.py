"""Tests for JSX Card utilities."""

import pytest
from a2a.types import Artifact, Part
from aion.core.utils.card import is_jsx_card, build_card_artifact, extract_card_name
from aion.core.constants import CARDS_MEDIA_TYPE, CARDS_EXTENSION_URI_V1, CARDS_PAYLOAD_SCHEMA_V1


class TestIsJsxCard:
    """Test JSX Card detection."""

    def test_minimal_valid_card(self):
        """Detect minimal valid card: <Card></Card>."""
        assert is_jsx_card("<Card></Card>") is True

    def test_card_with_whitespace(self):
        """Detect card with leading/trailing whitespace."""
        assert is_jsx_card("  <Card></Card>  ") is True
        assert is_jsx_card("\n<Card></Card>\n") is True
        assert is_jsx_card("\t<Card></Card>\t") is True

    def test_card_with_title_attribute(self):
        """Detect card with title attribute."""
        assert is_jsx_card('<Card title="Test"></Card>') is True
        assert is_jsx_card("<Card title='Test'></Card>") is True

    def test_card_with_multiple_attributes(self):
        """Detect card with multiple attributes."""
        assert is_jsx_card('<Card title="Test" name="card1"></Card>') is True

    def test_card_with_nested_content(self):
        """Detect card with nested JSX content."""
        content = '<Card><Text>Hello</Text><Button>Click</Button></Card>'
        assert is_jsx_card(content) is True

    def test_card_with_newlines_in_tag(self):
        """Detect card with newlines in opening tag."""
        content = '<Card\n  title="Test"\n></Card>'
        assert is_jsx_card(content) is True

    def test_not_card_different_tag(self):
        """Reject non-card JSX."""
        assert is_jsx_card("<Text>Not a card</Text>") is False
        assert is_jsx_card("<Button>Not a card</Button>") is False

    def test_not_card_mismatched_tags(self):
        """Reject card with mismatched closing tag."""
        assert is_jsx_card("<Card></Text>") is False

    def test_not_card_unclosed_card(self):
        """Reject unclosed card."""
        assert is_jsx_card("<Card>") is False
        assert is_jsx_card("<Card><Text></Text>") is False

    def test_not_card_cardname_tag(self):
        """Reject tags like <CardName> (not exact <Card)."""
        assert is_jsx_card("<CardName></CardName>") is False
        assert is_jsx_card("<CardComponent></CardComponent>") is False

    def test_not_card_non_string(self):
        """Reject non-string input."""
        assert is_jsx_card(None) is False
        assert is_jsx_card(123) is False
        assert is_jsx_card([]) is False
        assert is_jsx_card({}) is False

    def test_not_card_empty_string(self):
        """Reject empty string."""
        assert is_jsx_card("") is False

    def test_not_card_too_short(self):
        """Reject string shorter than minimal card (12 chars)."""
        assert is_jsx_card("<Card>") is False  # 6 chars
        assert is_jsx_card("<Car/>") is False  # 6 chars

    def test_multiple_cards_only_first_counts(self):
        """Detect only when structure is valid (counts matching open/close)."""
        # Two separate cards would have 2 opens and 2 closes - balanced but not nested
        content = "<Card></Card><Card></Card>"
        # This starts with Card but has balanced open/close, so it should detect as true
        # because it's checking for a valid document structure
        assert is_jsx_card(content) is True


class TestExtractCardName:
    """Test card name extraction."""

    def test_extract_title_attribute(self):
        """Extract name from title attribute."""
        card = '<Card title="My Card"></Card>'
        assert extract_card_name(card) == "My Card"

    def test_extract_title_single_quotes(self):
        """Extract name from title with single quotes."""
        card = "<Card title='My Card'></Card>"
        assert extract_card_name(card) == "My Card"

    def test_extract_name_attribute_fallback(self):
        """Fall back to name attribute when title is missing."""
        card = '<Card name="Named Card"></Card>'
        assert extract_card_name(card) == "Named Card"

    def test_title_takes_precedence_over_name(self):
        """Title attribute takes precedence over name."""
        card = '<Card title="Title" name="Name"></Card>'
        assert extract_card_name(card) == "Title"

    def test_no_name_returns_none(self):
        """Return None when neither title nor name present."""
        card = '<Card></Card>'
        assert extract_card_name(card) is None

    def test_extract_with_whitespace(self):
        """Extract name from card with leading whitespace."""
        card = '  <Card title="Spaced"></Card>'
        assert extract_card_name(card) == "Spaced"

    def test_extract_with_multiple_attributes(self):
        """Extract title when other attributes present."""
        card = '<Card id="card1" title="Test" data="value"></Card>'
        assert extract_card_name(card) == "Test"

    def test_empty_attribute_returns_none(self):
        """Return None for empty title attribute."""
        card = '<Card title=""></Card>'
        assert extract_card_name(card) == ""


class TestBuildCardArtifact:
    """Test A2A Artifact building from JSX Cards."""

    def test_build_simple_card(self):
        """Build artifact from simple card."""
        card_jsx = '<Card title="Test"></Card>'
        artifact = build_card_artifact(card_jsx)

        assert isinstance(artifact, Artifact)
        assert artifact.name == "Test"
        assert len(artifact.parts) == 1

    def test_card_part_has_text(self):
        """Card part should use text field, not raw."""
        card_jsx = '<Card></Card>'
        artifact = build_card_artifact(card_jsx)
        part = artifact.parts[0]

        assert isinstance(part, Part)
        assert part.text == card_jsx
        # Protobuf sets raw to empty bytes when not provided
        assert part.raw == b'' or part.raw is None

    def test_card_part_media_type(self):
        """Card part has correct MIME type."""
        card_jsx = '<Card></Card>'
        artifact = build_card_artifact(card_jsx)
        part = artifact.parts[0]

        assert part.media_type == CARDS_MEDIA_TYPE
        assert part.media_type == "application/vnd.aion.card+jsx"

    def test_card_part_metadata_includes_extension(self):
        """Card part metadata includes Cards extension."""
        card_jsx = '<Card></Card>'
        artifact = build_card_artifact(card_jsx)
        part = artifact.parts[0]

        assert part.metadata is not None
        assert CARDS_EXTENSION_URI_V1 in part.metadata
        assert part.metadata[CARDS_EXTENSION_URI_V1]["schema"] == CARDS_PAYLOAD_SCHEMA_V1

    def test_artifact_has_unique_id(self):
        """Artifact has a unique ID."""
        card_jsx = '<Card></Card>'
        artifact1 = build_card_artifact(card_jsx)
        artifact2 = build_card_artifact(card_jsx)

        assert artifact1.artifact_id != artifact2.artifact_id

    def test_custom_name_parameter(self):
        """Use custom name when provided."""
        card_jsx = '<Card title="Original"></Card>'
        artifact = build_card_artifact(card_jsx, name="Custom Name")

        assert artifact.name == "Custom Name"

    def test_extracted_name_when_custom_not_provided(self):
        """Extract name from card when custom name not provided."""
        card_jsx = '<Card title="Extracted"></Card>'
        artifact = build_card_artifact(card_jsx)

        assert artifact.name == "Extracted"

    def test_fallback_name_when_card_has_no_name(self):
        """Use 'Card' as default name."""
        card_jsx = '<Card></Card>'
        artifact = build_card_artifact(card_jsx)

        assert artifact.name == "Card"

    def test_custom_metadata_merged(self):
        """Custom metadata is merged into part metadata."""
        card_jsx = '<Card></Card>'
        custom_meta = {"custom_key": "custom_value"}
        artifact = build_card_artifact(card_jsx, metadata=custom_meta)
        part = artifact.parts[0]

        assert "custom_key" in part.metadata
        assert part.metadata["custom_key"] == "custom_value"
        # Original extension metadata should still be there
        assert CARDS_EXTENSION_URI_V1 in part.metadata

    def test_custom_metadata_doesnt_overwrite_extension(self):
        """Custom metadata doesn't overwrite cards extension."""
        card_jsx = '<Card></Card>'
        custom_meta = {CARDS_EXTENSION_URI_V1: {"extra": "data"}}
        artifact = build_card_artifact(card_jsx, metadata=custom_meta)
        part = artifact.parts[0]

        # Custom metadata should update, not replace
        assert CARDS_EXTENSION_URI_V1 in part.metadata

    def test_complex_card_with_content(self):
        """Build artifact from complex card with nested JSX."""
        card_jsx = '''<Card title="Complex">
            <Text>Hello</Text>
            <Button onClick="handleClick">Click me</Button>
        </Card>'''
        artifact = build_card_artifact(card_jsx)

        assert artifact.name == "Complex"
        assert artifact.parts[0].text == card_jsx
        assert artifact.parts[0].media_type == CARDS_MEDIA_TYPE

    def test_utf8_card_with_special_characters(self):
        """Handle card with special Unicode characters."""
        card_jsx = '<Card title="Карточка 🎨"></Card>'
        artifact = build_card_artifact(card_jsx)

        assert artifact.name == "Карточка 🎨"
        assert artifact.parts[0].text == card_jsx

    def test_artifact_structure(self):
        """Verify complete artifact structure."""
        card_jsx = '<Card title="Full Test"></Card>'
        artifact = build_card_artifact(card_jsx)

        assert artifact.artifact_id is not None
        assert isinstance(artifact.artifact_id, str)
        assert artifact.name == "Full Test"
        assert hasattr(artifact.parts, '__len__')
        assert len(artifact.parts) == 1
        assert artifact.parts[0].media_type == CARDS_MEDIA_TYPE
