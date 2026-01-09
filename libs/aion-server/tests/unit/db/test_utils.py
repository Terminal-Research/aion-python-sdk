from aion.shared.utils.db import convert_pg_url

class TestConvertPgUrl:
    """Test cases for convert_pg_url function."""

    def test_converts_postgresql_to_psycopg_format(self):
        """Test conversion to psycopg (SQLAlchemy) format."""
        url = "postgresql://user:pass@localhost:5432/dbname"
        expected = "postgresql+psycopg://user:pass@localhost:5432/dbname"
        assert convert_pg_url(url, driver="psycopg") == expected

    def test_converts_postgres_to_psycopg_format(self):
        """Test postgres:// prefix conversion to psycopg format."""
        url = "postgres://user:pass@localhost:5432/dbname"
        expected = "postgresql+psycopg://user:pass@localhost:5432/dbname"
        assert convert_pg_url(url, driver="psycopg") == expected

    def test_converts_to_psycopg2_format(self):
        """Test conversion to psycopg2 (SQLAlchemy) format."""
        url = "postgresql://user:pass@localhost:5432/dbname"
        expected = "postgresql+psycopg2://user:pass@localhost:5432/dbname"
        assert convert_pg_url(url, driver="psycopg2") == expected

    def test_converts_to_asyncpg_format(self):
        """Test conversion to asyncpg (SQLAlchemy) format."""
        url = "postgresql://user:pass@localhost:5432/dbname"
        expected = "postgresql+asyncpg://user:pass@localhost:5432/dbname"
        assert convert_pg_url(url, driver="asyncpg") == expected

    def test_converts_to_standard_format(self):
        """Test conversion to standard PostgreSQL format."""
        url = "postgresql+psycopg://user:pass@localhost:5432/dbname"
        expected = "postgresql://user:pass@localhost:5432/dbname"
        assert convert_pg_url(url, driver=None) == expected
        assert convert_pg_url(url) == expected  # Test default parameter

    def test_handles_empty_string(self):
        """Test edge case with empty string."""
        assert convert_pg_url("", driver="psycopg") == ""
        assert convert_pg_url("", driver=None) == ""

    def test_handles_url_without_protocol(self):
        """Test edge case with URL without protocol."""
        url = "no-protocol-here"
        assert convert_pg_url(url, driver="psycopg") == url
        assert convert_pg_url(url, driver=None) == url


class TestRoundTripConversion:
    """Test that conversions work correctly together."""

    def test_postgresql_url_round_trip_psycopg(self):
        """Test converting back and forth with psycopg driver."""
        original = "postgresql://user:pass@localhost:5432/dbname"
        converted = convert_pg_url(original, driver="psycopg")
        back = convert_pg_url(converted, driver=None)
        assert back == original

    def test_postgresql_url_round_trip_psycopg2(self):
        """Test converting back and forth with psycopg2 driver."""
        original = "postgresql://user:pass@localhost:5432/dbname"
        converted = convert_pg_url(original, driver="psycopg2")
        expected = "postgresql+psycopg2://user:pass@localhost:5432/dbname"
        assert converted == expected
        back = convert_pg_url(converted, driver=None)
        assert back == original

    def test_postgresql_url_round_trip_asyncpg(self):
        """Test converting back and forth with asyncpg driver."""
        original = "postgresql://user:pass@localhost:5432/dbname"
        converted = convert_pg_url(original, driver="asyncpg")
        expected = "postgresql+asyncpg://user:pass@localhost:5432/dbname"
        assert converted == expected
        back = convert_pg_url(converted, driver=None)
        assert back == original

    def test_postgres_url_gets_normalized(self):
        """Test that postgres:// gets normalized to postgresql:// after round trip."""
        original = "postgres://user:pass@localhost:5432/dbname"
        expected = "postgresql://user:pass@localhost:5432/dbname"
        converted = convert_pg_url(original, driver="psycopg")
        back = convert_pg_url(converted, driver=None)
        assert back == expected
