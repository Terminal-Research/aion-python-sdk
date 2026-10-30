"""The artifacts `aion.core.a2a.artifacts` builds for an agent to send."""

from __future__ import annotations

import pytest

from aion.core.a2a import text_artifact


class TestTextArtifact:
    def test_the_text_is_the_artifacts_only_part(self):
        artifact = text_artifact("pong")

        assert [part.text for part in artifact.parts] == ["pong"]
        assert artifact.name == "text"

    def test_every_call_is_a_new_artifact(self):
        assert text_artifact("one").artifact_id != text_artifact("two").artifact_id

    def test_the_name_and_id_can_be_given(self):
        artifact = text_artifact("pong", name="answer", artifact_id="answer-1")

        assert (artifact.name, artifact.artifact_id) == ("answer", "answer-1")

    def test_empty_text_is_kept(self):
        assert [part.text for part in text_artifact("").parts] == [""]

    def test_text_must_be_a_string(self):
        with pytest.raises(TypeError, match="'text' must be str"):
            text_artifact(b"pong")
