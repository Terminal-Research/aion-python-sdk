import pytest
from unittest.mock import patch
import os

from aion.server.configs import DbSettings


class TestDbSettings:
    """Test suite for DbSettings class focusing on critical functionality"""

    def test_empty_pg_url_returns_none(self):
        """Test that pg_url is None when POSTGRES_URL environment variable is not set"""
        with patch.dict(os.environ, {}, clear=True):
            settings = DbSettings()
            assert settings.pg_url is None
            assert settings.is_valid_pg_url() is False
            # Test that all URL parsing properties return None
            assert settings.pg_db_name is None
            assert settings.pg_user_name is None
            assert settings.pg_user_password is None
            assert settings.pg_host is None
            assert settings.pg_port is None
            assert settings.pg_sqlalchemy_url is None
            assert settings.pg_psycopg_url is None

    def test_environment_variable_loading(self):
        """Test that POSTGRES_URL environment variable is loaded correctly"""
        test_url = "postgresql://envuser:envpass@envhost:5433/envdb"

        with patch.dict(os.environ, {'POSTGRES_URL': test_url}):
            settings = DbSettings()
            assert settings.pg_url == test_url

    def test_valid_postgres_url_parsing_components(self):
        """Test parsing of URL components from a complete PostgreSQL URL"""
        test_url = "postgresql://testuser:testpass@localhost:5432/testdb"

        with patch.dict(os.environ, {'POSTGRES_URL': test_url}):
            settings = DbSettings()

            assert settings.pg_url == test_url
            assert settings.pg_db_name == "testdb"
            assert settings.pg_user_name == "testuser"
            assert settings.pg_user_password == "testpass"
            assert settings.pg_host == "localhost"
            assert settings.pg_port == 5432
            assert settings.is_valid_pg_url() is True
            # Test URL conversion properties work with valid URL
            assert settings.pg_sqlalchemy_url is not None
            assert settings.pg_psycopg_url is not None

    def test_minimal_valid_postgres_url(self):
        """Test parsing of minimal valid PostgreSQL URL"""
        test_url = "postgresql://localhost/db"

        with patch.dict(os.environ, {'POSTGRES_URL': test_url}):
            settings = DbSettings()

            assert settings.pg_url == test_url
            assert settings.pg_db_name == "db"
            assert settings.pg_host == "localhost"
            assert settings.pg_user_name is None
            assert settings.pg_user_password is None
            assert settings.pg_port is None

    def test_empty_url_validation(self):
        """Test that empty URL is handled correctly"""
        with patch.dict(os.environ, {'POSTGRES_URL': ''}):
            settings = DbSettings()
            assert settings.pg_url is None

    def test_invalid_postgres_url_raises_validation_error(self):
        """Test that invalid PostgreSQL URL format raises ValidationError"""
        invalid_url = "mysql://user:pass@localhost/db"

        with patch.dict(os.environ, {'POSTGRES_URL': invalid_url}):
            with pytest.raises(ValueError):
                DbSettings()

    def test_malformed_url_raises_validation_error(self):
        """Test that malformed URL raises ValidationError"""
        malformed_url = "postgresql://[invalid:url"

        with patch.dict(os.environ, {'POSTGRES_URL': malformed_url}):
            with pytest.raises(ValueError):
                DbSettings()

    def test_url_without_netloc_raises_validation_error(self):
        """Test that URL without proper netloc raises ValidationError"""
        malformed_url = "postgresql://"

        with patch.dict(os.environ, {'POSTGRES_URL': malformed_url}):
            with pytest.raises(ValueError):
                DbSettings()
