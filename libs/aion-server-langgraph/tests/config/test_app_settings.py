import pytest
from unittest.mock import patch
import os

from aion.shared.settings import AppSettings, app_settings


class TestAppSettings:
    """Test suite for AppSettings class"""

    def test_global_app_settings_instance(self):
        """Test that global app_settings instance exists"""
        assert app_settings is not None
        assert isinstance(app_settings, AppSettings)
        assert app_settings.log_level in ["DEBUG", "INFO", "WARNING", "ERROR"]

    def test_default_log_level(self):
        """Test that default log level is INFO when environment variable is not set"""
        with patch.dict(os.environ, {}, clear=True):
            settings = AppSettings()
            assert settings.log_level == "INFO"

    def test_valid_log_levels(self):
        """Test that all valid log levels are accepted"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]

        for level in valid_levels:
            with patch.dict(os.environ, {'LOG_LEVEL': level}):
                settings = AppSettings()
                assert settings.log_level == level

    def test_invalid_log_level_raises_validation_error(self):
        """Test that invalid log level raises ValidationError"""
        with patch.dict(os.environ, {'LOG_LEVEL': 'INVALID'}):
            with pytest.raises(ValueError):
                AppSettings()

    def test_default_docs_url(self):
        """Test that default docs URL is set correctly."""
        settings = AppSettings()
        assert settings.docs_url == "https://docs.aion.to/"

    @patch.dict(os.environ, {"AION_DOCS_URL": "https://custom.aion.docs/"})
    def test_docs_url_from_environment(self):
        """Test that docs URL can be overridden via environment variable."""
        settings = AppSettings()
        assert settings.docs_url == "https://custom.aion.docs/"

    def test_default_node_name(self):
        """Test that node_name defaults to None."""
        settings = AppSettings()
        assert settings.node_name is None

    @patch.dict(os.environ, {"NODE_NAME": "test-node-123"})
    def test_node_name_from_environment(self):
        """Test that node_name can be set via environment variable."""
        settings = AppSettings()
        assert settings.node_name == "test-node-123"

    def test_default_logstash_endpoint(self):
        """Test that logstash_endpoint defaults to None."""
        settings = AppSettings()
        assert settings.logstash_endpoint is None

    @patch.dict(os.environ, {"LOGSTASH_ENDPOINT": "localhost:5000"})
    def test_logstash_endpoint_from_environment(self):
        """Test that logstash_endpoint can be set via environment variable."""
        settings = AppSettings()
        assert settings.logstash_endpoint == "localhost:5000"
