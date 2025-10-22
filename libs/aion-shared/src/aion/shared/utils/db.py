__all__ = [
    "sqlalchemy_url",
    "psycopg_url",
]


def sqlalchemy_url(url: str) -> str:
    """Return a SQLAlchemy connection URL using the ``psycopg`` driver.

    SQLAlchemy requires the ``postgresql+psycopg://`` protocol when using
    ``psycopg`` version 3. This helper ensures the correct prefix is used
    without forcing callers to specify it in their environment variable.

    Args:
        url: The configured connection URL.

    Returns:
        The URL formatted for SQLAlchemy.
    """

    prefix = "postgresql+psycopg://"
    if url.startswith(prefix) or not url:
        return url
    if url.startswith("postgresql://"):
        return prefix + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return prefix + url[len("postgres://"):]
    return url


def psycopg_url(url: str) -> str:
    """Return a psycopg connection URL by removing SQLAlchemy-specific prefixes.

    psycopg expects standard PostgreSQL URLs without the '+psycopg' driver specification.
    This helper converts SQLAlchemy URLs back to standard PostgreSQL format.

    Args:
        url: The SQLAlchemy connection URL.

    Returns:
        The URL formatted for psycopg.
    """
    if not url or "://" not in url:
        return url

    _, rest = url.split("://", 1)
    return f"postgresql://{rest}"
