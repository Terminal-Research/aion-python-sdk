"""Helpers shared by test modules across the suites.

A test module imports from here, never from another test module: a builder
that two modules need lives in this package, next to nothing that pytest
collects.
"""
