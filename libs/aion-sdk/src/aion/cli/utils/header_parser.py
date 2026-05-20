"""Utilities for parsing HTTP-style headers from CLI input."""

from __future__ import annotations

from typing import Iterable

import asyncclick as click


def parse_headers(header_values: Iterable[str]) -> dict[str, str]:
    """Parse repeated ``key=value`` header CLI flags into a dictionary.

    Args:
        header_values: Header values supplied on the command line.

    Returns:
        Dictionary of parsed headers keyed by header name.
    """
    headers: dict[str, str] = {}
    for raw_value in header_values:
        if "=" not in raw_value:
            click.echo(
                f"Warning: Invalid header format '{raw_value}', expected 'key=value'"
            )
            continue

        key, value = raw_value.split("=", 1)
        headers[key] = value

    return headers
