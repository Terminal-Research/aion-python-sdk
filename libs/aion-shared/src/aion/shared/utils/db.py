from typing import Literal, Optional

__all__ = [
    "convert_pg_url",
]


def convert_pg_url(
    url: Optional["str"] = None,
    driver: Literal["psycopg", "psycopg2", "asyncpg"] | None = None,
) -> str:
    """Convert PostgreSQL connection URL to the format required by specific drivers.

    This function handles URL conversion between different PostgreSQL driver formats:
    - psycopg: Uses postgresql+psycopg:// prefix (SQLAlchemy with psycopg3)
    - psycopg2: Uses postgresql+psycopg2:// prefix (SQLAlchemy with psycopg2)
    - asyncpg: Uses postgresql+asyncpg:// prefix (SQLAlchemy with asyncpg)
    - None: Standard postgresql:// format (default)

    Args:
        url: The PostgreSQL connection URL to convert.
        driver: Target driver format. If None, returns standard postgresql:// format.

    Returns:
        The URL formatted for the specified driver.

    Examples:
        >>> convert_pg_url("postgresql://user:pass@host/db", "psycopg")
        'postgresql+psycopg://user:pass@host/db'
        >>> convert_pg_url("postgresql+psycopg://user:pass@host/db", None)
        'postgresql://user:pass@host/db'
        >>> convert_pg_url("postgres://user:pass@host/db", "psycopg2")
        'postgresql+psycopg2://user:pass@host/db'
        >>> convert_pg_url("postgresql://user:pass@host/db", "asyncpg")
        'postgresql+asyncpg://user:pass@host/db'
    """
    if not url or "://" not in url:
        return url

    # Split protocol and rest of URL
    protocol, rest = url.split("://", 1)

    # Convert to target format
    if driver == "psycopg":
        return f"postgresql+psycopg://{rest}"
    elif driver == "psycopg2":
        return f"postgresql+psycopg2://{rest}"
    elif driver == "asyncpg":
        return f"postgresql+asyncpg://{rest}"
    else:
        # None uses standard format
        return f"postgresql://{rest}"
