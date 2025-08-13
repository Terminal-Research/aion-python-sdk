import pytest
from pydantic import ValidationError

from aion.server.core.app import AppConfig


class TestAppConfig:
    """Test configuration validation and default values."""

    def test_default_values(self):
        """Test that default configuration values are set correctly."""
        config = AppConfig()

        assert config.host == "localhost"
        assert config.port == 10000

    def test_port_validation_boundaries(self):
        """Test port validation at boundary values."""
        # Valid boundary values
        config_min = AppConfig(port=1)
        assert config_min.port == 1

        config_max = AppConfig(port=65535)
        assert config_max.port == 65535

        # Invalid boundary values
        with pytest.raises(ValidationError):
            AppConfig(port=0)

        with pytest.raises(ValidationError):
            AppConfig(port=65536)

    def test_custom_host_and_port(self):
        """Test setting custom host and port values."""
        config = AppConfig(host="0.0.0.0", port=8080)

        assert config.host == "0.0.0.0"
        assert config.port == 8080
