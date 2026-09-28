"""Builders shared by unit test modules.

A test module imports from here, never from another test module: a builder
that two modules need lives in this package, next to nothing that pytest
collects. It is the unit suite's own; a builder the integration suite comes
to need as well moves up to a `tests/support` at that point, not before.
"""
