"""Tests for aion.shared.utils.logging — replace_uvicorn_loggers and replace_logstash_loggers.

Focus areas:
  replace_uvicorn_loggers:
    - creates loggers for uvicorn.access, uvicorn, uvicorn.error, starlette, fastapi
    - suppress_startup_logs=False leaves levels unchanged
    - suppress_startup_logs=True sets uvicorn / uvicorn.error to WARNING

  replace_logstash_loggers:
    - creates loggers for LogProcessingWorker, logstash_async.transport,
      logstash_async.memory_cache
"""

import logging
from unittest.mock import MagicMock, call, patch

import pytest

from aion.server.utils.logging import replace_logstash_loggers, replace_uvicorn_loggers

class TestReplaceUvicornLoggers:
    def _patch_get_logger(self):
        """Return a patcher that intercepts calls to get_logger.

        get_logger is imported lazily inside the function body, so we patch
        the factory module directly rather than the utils.logging namespace.
        """
        return patch("aion.shared.logging.factory.get_logger", autospec=True)

    @pytest.mark.parametrize("logger_name", [
        "uvicorn.access", "uvicorn", "uvicorn.error", "starlette", "fastapi",
    ])
    def test_creates_expected_loggers(self, logger_name):
        """replace_uvicorn_loggers creates loggers for uvicorn, starlette, and fastapi."""
        with self._patch_get_logger() as mock_get:
            replace_uvicorn_loggers()
        names = [c.args[0] for c in mock_get.call_args_list]
        assert logger_name in names

    def test_uvicorn_access_has_logstash_enabled(self):
        """uvicorn.access logger has use_logstash=True."""
        with self._patch_get_logger() as mock_get:
            replace_uvicorn_loggers()
        access_call = next(
            c for c in mock_get.call_args_list if c.args[0] == "uvicorn.access"
        )
        assert access_call.kwargs.get("use_logstash") is True

    def test_other_loggers_have_logstash_disabled(self):
        """Non-access uvicorn loggers have use_logstash=False."""
        with self._patch_get_logger() as mock_get:
            replace_uvicorn_loggers()
        for c in mock_get.call_args_list:
            name = c.args[0]
            if name != "uvicorn.access":
                assert c.kwargs.get("use_logstash") is False, (
                    f"Expected use_logstash=False for {name}"
                )

    def test_suppress_startup_logs_true_sets_warning(self):
        """suppress_startup_logs=True sets uvicorn and uvicorn.error loggers to WARNING level."""
        mock_uvicorn = MagicMock()
        mock_uvicorn_error = MagicMock()

        def fake_get_logger(name, **kwargs):
            if name == "uvicorn":
                return mock_uvicorn
            if name == "uvicorn.error":
                return mock_uvicorn_error
            return MagicMock()

        with patch("aion.shared.logging.factory.get_logger", side_effect=fake_get_logger):
            replace_uvicorn_loggers(suppress_startup_logs=True)

        mock_uvicorn.setLevel.assert_called_once_with(logging.WARNING)
        mock_uvicorn_error.setLevel.assert_called_once_with(logging.WARNING)

    @pytest.mark.parametrize("explicit_arg", [False, None])
    def test_suppress_startup_logs_false_or_default_does_not_change_level(self, explicit_arg):
        """suppress_startup_logs=False (or default) does not change logger levels."""
        mock_uvicorn = MagicMock()
        mock_uvicorn_error = MagicMock()

        def fake_get_logger(name, **kwargs):
            if name == "uvicorn":
                return mock_uvicorn
            if name == "uvicorn.error":
                return mock_uvicorn_error
            return MagicMock()

        with patch("aion.shared.logging.factory.get_logger", side_effect=fake_get_logger):
            if explicit_arg is None:
                replace_uvicorn_loggers()
            else:
                replace_uvicorn_loggers(suppress_startup_logs=explicit_arg)

        mock_uvicorn.setLevel.assert_not_called()
        mock_uvicorn_error.setLevel.assert_not_called()

class TestReplaceLogstashLoggers:
    def _patch_get_logger(self):
        return patch("aion.shared.logging.factory.get_logger", autospec=True)

    @pytest.mark.parametrize("logger_name", [
        "LogProcessingWorker",
        "logstash_async.transport",
        "logstash_async.memory_cache",
    ])
    def test_creates_expected_loggers(self, logger_name):
        """replace_logstash_loggers creates loggers for LogProcessingWorker and logstash_async modules."""
        with self._patch_get_logger() as mock_get:
            replace_logstash_loggers()
        names = [c.args[0] for c in mock_get.call_args_list]
        assert logger_name in names

    def test_all_loggers_use_stream_only(self):
        """All loggers created by replace_logstash_loggers have use_stream=True and use_logstash=False."""
        with self._patch_get_logger() as mock_get:
            replace_logstash_loggers()
        for c in mock_get.call_args_list:
            assert c.kwargs.get("use_stream") is True
            assert c.kwargs.get("use_logstash") is False

    def test_creates_exactly_three_loggers(self):
        """replace_logstash_loggers creates exactly three loggers."""
        with self._patch_get_logger() as mock_get:
            replace_logstash_loggers()
        assert mock_get.call_count == 3
