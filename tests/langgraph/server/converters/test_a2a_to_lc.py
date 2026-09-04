import base64
import json
from unittest.mock import Mock

import pytest
from a2a.types import Part
from google.protobuf import json_format, struct_pb2

from aion.langgraph.server.converters.a2a_to_lc import A2AToLcConverter


class TestFromParts:
    """from_parts converts a list of Parts, skipping unrecognized ones."""

    def test_multiple_text_parts_all_converted(self):
        """Each text Part in the list produces a content block."""
        parts = [Part(text="Hello"), Part(text="World")]
        result = A2AToLcConverter.from_parts(parts)
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        """Empty input list produces empty output."""
        assert A2AToLcConverter.from_parts([]) == []


class TestFromPart:
    """from_part dispatches to the correct block type by Part field presence."""

    def test_text_part_returns_text_block(self):
        """Part with text produces a text content block."""
        result = A2AToLcConverter.from_part(Part(text="Hello agent"))
        assert result is not None
        assert result.get("type") == "text"
        assert result.get("text") == "Hello agent"

    def test_raw_part_returns_file_block_with_base64_data(self):
        """Part with raw bytes produces a file block with base64-encoded data."""
        raw = b"binary content"
        result = A2AToLcConverter.from_part(Part(raw=raw, media_type="image/png"))
        assert result is not None
        expected_b64 = base64.b64encode(raw).decode()
        # base64 data must be present in the block
        assert result.get("data") == expected_b64 or result.get("base64") == expected_b64

    def test_url_part_returns_file_block_with_url(self):
        """Part with url produces a file block whose url matches."""
        result = A2AToLcConverter.from_part(Part(url="https://example.com/doc.pdf", media_type="application/pdf"))
        assert result is not None
        assert result.get("url") == "https://example.com/doc.pdf"

    def test_data_part_returns_text_block_with_json(self):
        """Part with protobuf data field produces a text block containing JSON."""
        proto_val = struct_pb2.Value()
        json_format.ParseDict({"score": 0.9, "label": "positive"}, proto_val)
        result = A2AToLcConverter.from_part(Part(data=proto_val))
        assert result is not None
        assert result.get("type") == "text"
        parsed = json.loads(result.get("text", "{}"))
        assert parsed.get("score") == 0.9
        assert parsed.get("label") == "positive"

    def test_part_with_no_text_url_raw_falls_through_to_data_branch(self):
        """Part() with no text/raw/url always hits the data branch (proto data field is always set).
        This means from_part returns a JSON text block, never None, for real Part objects."""
        result = A2AToLcConverter.from_part(Part())
        assert result is not None
        assert result.get("type") == "text"
        # empty proto data serializes to '{}'
        assert result.get("text") == "{}"

    def test_raw_part_mime_type_is_detected(self):
        """Raw Part without explicit media_type uses _detect_mime_type fallback."""
        raw = b"data"
        result = A2AToLcConverter.from_part(Part(raw=raw))
        assert result is not None
        # should use "application/octet-stream" as fallback mime type
        assert result.get("mime_type") == "application/octet-stream"



class TestDetectMimeType:
    """_detect_mime_type follows explicit > filename guess > fallback priority."""

    def test_explicit_media_type_is_used(self):
        """Explicit media_type on Part takes priority over everything else."""
        part = Part(url="file.bin", media_type="application/pdf")
        assert A2AToLcConverter._detect_mime_type(part) == "application/pdf"

    def test_filename_extension_is_guessed_when_no_media_type(self):
        """Without media_type, mime is guessed from filename extension."""
        part = Mock()
        part.media_type = None
        part.filename = "photo.png"
        assert A2AToLcConverter._detect_mime_type(part) == "image/png"

    def test_unknown_extension_falls_back_to_octet_stream(self):
        """Unknown filename extension yields the fallback mime type."""
        part = Mock()
        part.media_type = None
        part.filename = "archive.xyzformat"
        result = A2AToLcConverter._detect_mime_type(part)
        assert result == "application/octet-stream"

    def test_no_filename_no_media_type_falls_back_to_octet_stream(self):
        """No filename and no media_type yields the fallback mime type."""
        part = Mock()
        part.media_type = None
        part.filename = None
        assert A2AToLcConverter._detect_mime_type(part) == "application/octet-stream"
