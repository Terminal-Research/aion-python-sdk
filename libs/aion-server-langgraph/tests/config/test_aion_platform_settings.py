import os
from unittest.mock import patch
from aion.server.configs.aion_platform import aion_platform_settings, AionPlatformSettings

class TestAionPlatformSettings:
    def test_default_docs_url(self):
        """Test that default docs URL is set correctly."""
        settings = AionPlatformSettings()
        assert settings.docs_url == "https://docs.aion.to/"

    @patch.dict(os.environ, {"AION_DOCS_URL": "https://custom.aion.docs/"})
    def test_docs_url_from_environment(self):
        """Test that docs URL can be overridden via environment variable."""
        settings = AionPlatformSettings()
        assert settings.docs_url == "https://custom.aion.docs/"

    def test_global_settings_instance(self):
        """Test that global settings instance exists and is properly configured."""
        assert aion_platform_settings is not None
        assert isinstance(aion_platform_settings, AionPlatformSettings)
