from aion.server.db.utils import sqlalchemy_url, psycopg_url

class TestSQLAlchemyUrl:
    """Test cases for sqlalchemy_url function."""

    def test_converts_postgresql_to_sqlalchemy_format(self):
        """Test main conversion functionality."""
        url = "postgresql://user:pass@localhost:5432/dbname"
        expected = "postgresql+psycopg://user:pass@localhost:5432/dbname"
        assert sqlalchemy_url(url) == expected

    def test_converts_postgres_to_sqlalchemy_format(self):
        """Test postgres:// prefix conversion."""
        url = "postgres://user:pass@localhost:5432/dbname"
        expected = "postgresql+psycopg://user:pass@localhost:5432/dbname"
        assert sqlalchemy_url(url) == expected

    def test_returns_unchanged_when_already_sqlalchemy_format(self):
        """Test idempotency - already formatted URLs stay unchanged."""
        url = "postgresql+psycopg://user:pass@localhost:5432/dbname"
        assert sqlalchemy_url(url) == url

    def test_returns_unchanged_for_non_postgresql_urls(self):
        """Test non-PostgreSQL URLs are not modified."""
        url = "mysql://user:pass@localhost/dbname"
        assert sqlalchemy_url(url) == url

    def test_handles_empty_string(self):
        """Test edge case with empty string."""
        assert sqlalchemy_url("") == ""


class TestPsycopgUrl:
    """Test cases for psycopg_url function."""

    def test_removes_sqlalchemy_prefix(self):
        """Test main functionality - removing SQLAlchemy prefix."""
        url = "postgresql+psycopg://user:pass@localhost:5432/dbname"
        expected = "postgresql://user:pass@localhost:5432/dbname"
        assert psycopg_url(url) == expected

    def test_converts_any_protocol_to_postgresql(self):
        """Test that any protocol gets normalized to postgresql://."""
        url = "mysql://user:pass@localhost/dbname"
        expected = "postgresql://user:pass@localhost/dbname"
        assert psycopg_url(url) == expected

    def test_handles_malformed_urls(self):
        """Test edge cases with malformed URLs."""
        assert psycopg_url("no-protocol-here") == "no-protocol-here"
        assert psycopg_url("") == ""
        assert psycopg_url(None) is None


class TestRoundTripConversion:
    """Test that conversions work correctly together."""

    def test_postgresql_url_round_trip(self):
        """Test converting back and forth preserves the URL."""
        original = "postgresql://user:pass@localhost:5432/dbname"
        converted = sqlalchemy_url(original)
        back = psycopg_url(converted)
        assert back == original

    def test_postgres_url_gets_normalized(self):
        """Test that postgres:// gets normalized to postgresql:// after round trip."""
        original = "postgres://user:pass@localhost:5432/dbname"
        expected = "postgresql://user:pass@localhost:5432/dbname"
        converted = sqlalchemy_url(original)
        back = psycopg_url(converted)
        assert back == expected
