"""Tests for BaseService and BaseExecuteService."""

import logging
import pytest
from unittest.mock import MagicMock, patch

from aion.shared.services import BaseService, BaseExecuteService


class _ConcreteService(BaseService):
    pass


class _ConcreteExecuteService(BaseExecuteService):
    async def execute(self, *args, **kwargs):
        return ("called", args, kwargs)


class TestBaseService:
    def test_default_logger_created_when_none_provided(self):
        """Verify that default logger created when none provided."""
        svc = _ConcreteService()
        assert svc.logger is not None
        assert isinstance(svc.logger, logging.Logger)

    def test_default_logger_uses_module_name(self):
        """Verify that default logger uses module name."""
        svc = _ConcreteService()
        assert svc.logger.name == _ConcreteService.__module__

    def test_custom_logger_is_stored(self):
        """Verify that custom logger is stored."""
        custom_logger = logging.getLogger("custom.test")
        svc = _ConcreteService(logger=custom_logger)
        assert svc.logger is custom_logger

    def test_base_service_has_no_abstract_methods(self):
        """Verify that base service has no abstract methods."""
        # BaseService has no @abstractmethod — subclassing is optional
        class _Minimal(BaseService):
            pass

        svc = _Minimal()
        assert isinstance(svc, BaseService)

    def test_get_logger_called_when_no_logger_passed(self):
        """Verify that get logger called when no logger passed."""
        with patch("aion.shared.services.get_logger") as mock_get_logger:
            mock_get_logger.return_value = MagicMock(spec=logging.Logger)
            svc = _ConcreteService()
            mock_get_logger.assert_called_once_with(_ConcreteService.__module__)
            assert svc.logger is mock_get_logger.return_value


class TestBaseExecuteService:
    def test_cannot_instantiate_without_execute(self):
        """Verify that cannot instantiate without execute."""
        class _Incomplete(BaseExecuteService):
            pass

        with pytest.raises(TypeError):
            _Incomplete()

    @pytest.mark.asyncio
    async def test_execute_returns_result(self):
        """Verify that execute returns result."""
        svc = _ConcreteExecuteService()
        result = await svc.execute("a", "b", key="val")
        assert result == ("called", ("a", "b"), {"key": "val"})

    @pytest.mark.asyncio
    async def test_execute_no_args(self):
        """Verify that execute no args."""
        svc = _ConcreteExecuteService()
        result = await svc.execute()
        assert result == ("called", (), {})

    def test_inherits_logger_from_base_service(self):
        """Verify that inherits logger from base service."""
        svc = _ConcreteExecuteService()
        assert isinstance(svc.logger, logging.Logger)

    def test_custom_logger_passed_to_base(self):
        """Verify that custom logger passed to base."""
        custom_logger = logging.getLogger("execute.test")
        svc = _ConcreteExecuteService(logger=custom_logger)
        assert svc.logger is custom_logger

    @pytest.mark.asyncio
    async def test_multiple_instances_independent(self):
        """Verify that multiple instances independent."""
        svc1 = _ConcreteExecuteService()
        svc2 = _ConcreteExecuteService()
        r1 = await svc1.execute(1)
        r2 = await svc2.execute(2)
        assert r1 == ("called", (1,), {})
        assert r2 == ("called", (2,), {})
