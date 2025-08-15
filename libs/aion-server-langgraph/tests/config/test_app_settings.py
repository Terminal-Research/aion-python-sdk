import pytest
from unittest.mock import patch
import os

from aion.server.configs import AppSettings


class TestAppSettings:
    """Test suite for AppSettings class"""

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
