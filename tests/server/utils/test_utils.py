"""Tests for utility functions: url, templates, text, path, asyncio."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from aion.server.utils.asyncio import has_event_loop
from aion.core.utils.path import get_base_dir, get_config_path
from aion.core.utils.text import colorize_text
from aion.core.utils.url import parse_host_port


class TestParseHostPort:
    def test_simple_host_port(self):
        """Verify that simple host port."""
        assert parse_host_port("localhost:8080") == ("localhost", 8080)

    def test_url_with_scheme(self):
        """Verify that URL with scheme."""
        assert parse_host_port("https://example.com:443") == ("example.com", 443)

    def test_http_scheme(self):
        """Verify that HTTP scheme."""
        assert parse_host_port("http://myhost:9000") == ("myhost", 9000)

    def test_empty_string_returns_none(self):
        """Verify that empty string returns none."""
        assert parse_host_port("") is None

    def test_scheme_without_port_returns_none(self):
        """Verify that scheme without port returns none."""
        # urlparse sees no port
        assert parse_host_port("https://example.com") is None

    def test_host_without_port_returns_none(self):
        """Verify that host without port returns none."""
        # No colon at all
        assert parse_host_port("localhost") is None

    def test_ipv4_address(self):
        """Verify that ipv4 address."""
        assert parse_host_port("127.0.0.1:5432") == ("127.0.0.1", 5432)

    def test_leading_trailing_whitespace_is_stripped(self):
        """Verify that leading trailing whitespace is stripped."""
        assert parse_host_port("  localhost:8080  ") == ("localhost", 8080)


class TestColorizeText:
    def test_reset_color_wraps_with_reset_codes(self):
        """Verify that reset color wraps with reset codes."""
        result = colorize_text("hello", "reset")
        assert "hello" in result
        assert "\033[0m" in result

    def test_red_color_contains_red_code(self):
        """Verify that red color contains red code."""
        result = colorize_text("err", "red")
        assert result.startswith("\033[31m")
        assert result.endswith("\033[0m")

    def test_green_color(self):
        """Verify that green color."""
        result = colorize_text("ok", "green")
        assert "\033[32m" in result

    def test_all_named_colors_produce_non_empty_output(self):
        """Verify that all named colors produce non empty output."""
        colors = [
            "red", "green", "yellow", "orange", "blue", "magenta", "cyan",
            "light_grey", "bright_grey", "bright_red", "bright_green",
            "bright_yellow", "bright_blue", "reset",
        ]
        for color in colors:
            result = colorize_text("x", color)
            assert "x" in result, f"text missing for color {color}"

    def test_text_preserved_between_codes(self):
        """Verify that text preserved between codes."""
        result = colorize_text("sample text", "blue")
        assert "sample text" in result

    def test_empty_text(self):
        """Verify that empty text."""
        result = colorize_text("", "cyan")
        assert isinstance(result, str)


class TestGetBaseDir:
    def test_returns_path_object(self):
        """Verify that returns path object."""
        result = get_base_dir()
        assert isinstance(result, Path)

    def test_returns_absolute_path(self):
        """Verify that returns absolute path."""
        result = get_base_dir()
        assert result.is_absolute()

    def test_matches_cwd(self, tmp_path, monkeypatch):
        """Verify that matches current working directory."""
        monkeypatch.chdir(tmp_path)
        assert get_base_dir() == tmp_path


class TestGetConfigPath:
    def test_default_filename_is_aion_yaml(self, tmp_path, monkeypatch):
        """Verify that default filename is aion YAML."""
        monkeypatch.chdir(tmp_path)
        result = get_config_path()
        assert result == tmp_path / "aion.yaml"

    def test_relative_path_resolved_against_cwd(self, tmp_path, monkeypatch):
        """Verify that relative path resolved against current working directory."""
        monkeypatch.chdir(tmp_path)
        result = get_config_path("custom.yaml")
        assert result == tmp_path / "custom.yaml"

    def test_absolute_path_returned_unchanged(self, tmp_path):
        """Verify that absolute path returned unchanged."""
        abs_path = tmp_path / "abs_config.yaml"
        result = get_config_path(abs_path)
        assert result == abs_path

    def test_path_object_input(self, tmp_path, monkeypatch):
        """Verify that path object input."""
        monkeypatch.chdir(tmp_path)
        result = get_config_path(Path("myconf.yaml"))
        assert result == tmp_path / "myconf.yaml"

    def test_result_is_absolute(self, tmp_path, monkeypatch):
        """Verify that result is absolute."""
        monkeypatch.chdir(tmp_path)
        result = get_config_path("relative/path.yaml")
        assert result.is_absolute()


class TestHasEventLoop:
    def test_returns_false_outside_loop(self):
        """Verify that returns false outside loop."""
        assert has_event_loop() is False

    def test_returns_true_inside_running_loop(self):
        """Verify that returns true inside running loop."""
        async def _check():
            return has_event_loop()

        assert asyncio.run(_check()) is True
