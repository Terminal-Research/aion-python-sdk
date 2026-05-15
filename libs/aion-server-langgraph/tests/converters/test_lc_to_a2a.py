import base64
from unittest.mock import Mock, patch

import pytest
from a2a.types import Part

from aion.server_langgraph.converters.lc_to_a2a import LcToA2AConverter


def make_mock_message(content_blocks):
    """Create a mock BaseMessage with given content_blocks list."""
    msg = Mock()
    msg.content_blocks = content_blocks
    return msg


def make_block(type_, **kwargs):
    """Build a content block dict as LangChain produces them."""
    return {"type": type_, **kwargs}


class TestFromMessage:
    """from_message iterates content_blocks and collects non-None Part results."""

    def test_message_with_text_block_returns_one_part(self):
        """Single text block yields a single Part with text set."""
        msg = make_mock_message([make_block("text", text="Hello")])
        parts = LcToA2AConverter.from_message(msg)
        assert len(parts) == 1
        assert parts[0].text == "Hello"

    def test_message_with_multiple_blocks_returns_multiple_parts(self):
        """Multiple convertible blocks produce one Part each."""
        msg = make_mock_message([
            make_block("text", text="A"),
            make_block("text", text="B"),
        ])
        parts = LcToA2AConverter.from_message(msg)
        assert len(parts) == 2

    def test_tool_blocks_are_excluded(self):
        """Tool-related blocks are skipped; only text block is returned."""
        msg = make_mock_message([
            make_block("tool_call", name="fn", args={}),
            make_block("text", text="Result"),
        ])
        parts = LcToA2AConverter.from_message(msg)
        assert len(parts) == 1
        assert parts[0].text == "Result"

    def test_empty_content_blocks_returns_empty_list(self):
        """Message with no content blocks returns an empty list."""
        msg = make_mock_message([])
        parts = LcToA2AConverter.from_message(msg)
        assert parts == []


class TestFromBlock:
    """from_block dispatches by block type and returns Part or None."""

    def test_tool_call_block_returns_none(self):
        """tool_call blocks are silently skipped."""
        result = LcToA2AConverter.from_block(make_block("tool_call", name="f", args={}))
        assert result is None

    def test_server_tool_call_block_returns_none(self):
        """server_tool_call blocks are silently skipped."""
        result = LcToA2AConverter.from_block(make_block("server_tool_call"))
        assert result is None

    def test_invalid_tool_call_block_returns_none(self):
        """invalid_tool_call blocks are silently skipped."""
        result = LcToA2AConverter.from_block(make_block("invalid_tool_call"))
        assert result is None

    def test_reasoning_block_excluded_by_default(self):
        """Reasoning blocks are not included when include_reasoning=False (default)."""
        result = LcToA2AConverter.from_block(make_block("reasoning", reasoning="thinking..."))
        assert result is None

    def test_reasoning_block_included_when_flag_set(self):
        """Reasoning blocks produce a Part when include_reasoning=True."""
        result = LcToA2AConverter.from_block(
            make_block("reasoning", reasoning="deep thought", id="r-1"),
            include_reasoning=True,
        )
        assert result is not None
        assert result.text == "deep thought"
        assert result.metadata["lc_block_type"] == "reasoning"

    def test_text_block_returns_text_part(self):
        """text block produces a Part with text field set."""
        result = LcToA2AConverter.from_block(make_block("text", text="hi"))
        assert result.text == "hi"

    def test_image_block_delegates_to_data_content(self):
        """image block goes through _from_data_content."""
        block = make_block("image", source_type="url", url="https://example.com/img.png")
        result = LcToA2AConverter.from_block(block)
        assert result.url == "https://example.com/img.png"

    def test_unknown_block_type_returns_fallback_part(self):
        """Unrecognized block types are wrapped as a DataPart with lc_block_type metadata."""
        result = LcToA2AConverter.from_block(make_block("custom_type", value="x"))
        assert result is not None
        assert result.metadata["lc_block_type"] == "custom_type"


class TestFromDataContent:
    """_from_data_content dispatches by source_type."""

    def test_url_source_type_produces_url_part(self):
        """source_type='url' maps to Part(url=..., media_type=...)."""
        block = make_block("image", source_type="url", url="https://img.png", mime_type="image/png")
        result = LcToA2AConverter._from_data_content(block)
        assert result.url == "https://img.png"
        assert result.media_type == "image/png"

    def test_base64_source_type_decodes_to_raw_bytes(self):
        """source_type='base64' decodes the data field and sets Part.raw."""
        raw = b"binary data"
        encoded = base64.b64encode(raw).decode()
        block = make_block("file", source_type="base64", data=encoded, mime_type="application/pdf")
        result = LcToA2AConverter._from_data_content(block)
        assert result.raw == raw
        assert result.media_type == "application/pdf"

    def test_base64_without_mime_type_uses_octet_stream(self):
        """source_type='base64' without mime_type defaults to 'application/octet-stream'."""
        block = make_block("file", source_type="base64", data=base64.b64encode(b"x").decode())
        result = LcToA2AConverter._from_data_content(block)
        assert result.media_type == "application/octet-stream"

    def test_id_source_type_produces_url_part(self):
        """source_type='id' maps the id field to Part.url."""
        block = make_block("image", source_type="id", id="file-abc-123")
        result = LcToA2AConverter._from_data_content(block)
        assert result.url == "file-abc-123"

    def test_plain_text_source_type_produces_text_part(self):
        """source_type='plain_text' maps to Part(text=...)."""
        block = make_block("file", source_type="plain_text", text="plain content")
        result = LcToA2AConverter._from_data_content(block)
        assert result.text == "plain content"

    def test_unknown_source_type_returns_fallback_part(self):
        """Unknown source_type falls back to a DataPart with lc_block_type metadata."""
        block = make_block("image", source_type="unknown_source")
        result = LcToA2AConverter._from_data_content(block)
        assert result is not None
        assert result.metadata["lc_block_type"] == "image"
