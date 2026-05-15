"""Tests for AionConfigReader.

Uses temp files so tests are hermetic and need no real filesystem state.
"""

import textwrap
from pathlib import Path

import pytest

from aion.shared.config.exceptions import ConfigurationError
from aion.shared.config.models import AionConfig
from aion.shared.config.reader import AionConfigReader


def _reader(tmp_path: Path, content: str) -> AionConfigReader:
    cfg = tmp_path / "aion.yaml"
    cfg.write_text(textwrap.dedent(content), encoding="utf-8")
    return AionConfigReader(config_path=cfg)


VALID_YAML = """\
    aion:
      agents:
        my_agent:
          path: my.module:Agent
          version: "1.0.0"
"""


class TestLoadConfigFile:
    def test_missing_file_raises(self, tmp_path):
        """load_config_file raises ConfigurationError with 'not found' for a missing file."""
        reader = AionConfigReader(config_path=tmp_path / "nonexistent.yaml")
        with pytest.raises(ConfigurationError, match="not found"):
            reader.load_config_file()

    def test_empty_file_raises(self, tmp_path):
        """load_config_file raises ConfigurationError with 'empty' for an empty YAML file."""
        reader = _reader(tmp_path, "")
        with pytest.raises(ConfigurationError, match="empty"):
            reader.load_config_file()

    def test_non_dict_root_raises(self, tmp_path):
        """load_config_file raises ConfigurationError with 'mapping' for a YAML list at root."""
        reader = _reader(tmp_path, "- item1\n- item2\n")
        with pytest.raises(ConfigurationError, match="mapping"):
            reader.load_config_file()

    def test_invalid_yaml_raises(self, tmp_path):
        """load_config_file raises ConfigurationError with 'YAML' for malformed YAML."""
        reader = _reader(tmp_path, "key: [unclosed bracket\n")
        with pytest.raises(ConfigurationError, match="YAML"):
            reader.load_config_file()

    def test_valid_yaml_returns_dict(self, tmp_path):
        """load_config_file returns a dict with an 'aion' key for valid YAML."""
        reader = _reader(tmp_path, VALID_YAML)
        data = reader.load_config_file()
        assert isinstance(data, dict)
        assert "aion" in data


class TestValidateAndParseConfig:
    def test_missing_aion_key_raises(self, tmp_path):
        """validate_and_parse_config raises ConfigurationError with \"'aion' section\" when 'aion' key is absent."""
        reader = _reader(tmp_path, VALID_YAML)
        with pytest.raises(ConfigurationError, match="'aion' section"):
            reader.validate_and_parse_config({"no_aion": {}})

    def test_aion_key_not_dict_raises(self, tmp_path):
        """validate_and_parse_config raises ConfigurationError when 'aion' value is not a dict."""
        reader = _reader(tmp_path, VALID_YAML)
        with pytest.raises(ConfigurationError):
            reader.validate_and_parse_config({"aion": "scalar"})

    def test_valid_config_returns_aion_config(self, tmp_path):
        """validate_and_parse_config returns an AionConfig with the expected agents for valid input."""
        reader = _reader(tmp_path, VALID_YAML)
        raw = reader.load_config_file()
        config = reader.validate_and_parse_config(raw)
        assert isinstance(config, AionConfig)
        assert "my_agent" in config.agents

    def test_invalid_agent_version_raises_config_error(self, tmp_path):
        """validate_and_parse_config raises ConfigurationError with 'validation failed' for an invalid agent version."""
        reader = _reader(tmp_path, VALID_YAML)
        with pytest.raises(ConfigurationError, match="validation failed"):
            reader.validate_and_parse_config({
                "aion": {
                    "agents": {
                        "bad": {"path": "x:Y", "version": "bad-version"}
                    }
                }
            })


class TestLoadAndValidateConfig:
    def test_full_pipeline_returns_aion_config(self, tmp_path):
        """load_and_validate_config runs the full pipeline and returns an AionConfig with agents."""
        reader = _reader(tmp_path, VALID_YAML)
        config = reader.load_and_validate_config()
        assert isinstance(config, AionConfig)
        assert len(config.agents) == 1

    def test_missing_file_raises_config_error(self, tmp_path):
        """load_and_validate_config raises ConfigurationError for a missing YAML file."""
        reader = AionConfigReader(config_path=tmp_path / "missing.yaml")
        with pytest.raises(ConfigurationError):
            reader.load_and_validate_config()


class TestValidateAgentConfig:
    def test_valid_data_returns_agent_config(self, tmp_path):
        """validate_agent_config returns an AgentConfig with the correct path for valid input."""
        reader = _reader(tmp_path, VALID_YAML)
        agent = reader.validate_agent_config({"path": "mod:Class"})
        assert agent.path == "mod:Class"

    def test_invalid_version_raises_config_error(self, tmp_path):
        """validate_agent_config raises ConfigurationError with 'validation failed' for an invalid version."""
        reader = _reader(tmp_path, VALID_YAML)
        with pytest.raises(ConfigurationError, match="validation failed"):
            reader.validate_agent_config({"path": "mod:Class", "version": "bad"})

    def test_missing_required_path_raises_config_error(self, tmp_path):
        """validate_agent_config raises ConfigurationError when path is missing from the input dict."""
        reader = _reader(tmp_path, VALID_YAML)
        with pytest.raises(ConfigurationError):
            reader.validate_agent_config({})
