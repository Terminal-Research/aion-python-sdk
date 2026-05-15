"""Tests for agent exception hierarchy."""

import pytest

from aion.shared.agent.exceptions import (
    AdapterError,
    AdapterNotFoundError,
    AdapterRegistrationError,
    ConfigurationError,
    ExecutionError,
    MessageConversionError,
    StateRetrievalError,
    UnsupportedOperationError,
)


class TestHierarchy:
    def test_adapter_error_is_exception(self):
        """AdapterError is a subclass of the built-in Exception class."""
        assert issubclass(AdapterError, Exception)

    def test_all_errors_inherit_adapter_error(self):
        """All specific adapter error classes inherit from AdapterError."""
        for cls in (
            AdapterNotFoundError,
            AdapterRegistrationError,
            ExecutionError,
            StateRetrievalError,
            MessageConversionError,
            ConfigurationError,
            UnsupportedOperationError,
        ):
            assert issubclass(cls, AdapterError), f"{cls.__name__} must inherit AdapterError"

    def test_all_errors_are_catchable_as_adapter_error(self):
        """Every specific error can be caught with an AdapterError handler."""
        errors = [
            AdapterNotFoundError(framework_name="fw"),
            AdapterRegistrationError("msg"),
            ExecutionError("msg"),
            StateRetrievalError("msg"),
            MessageConversionError("msg"),
            ConfigurationError("msg"),
            UnsupportedOperationError("op", "fw"),
        ]
        for exc in errors:
            with pytest.raises(AdapterError):
                raise exc


class TestAdapterNotFoundError:
    def test_with_framework_name(self):
        """AdapterNotFoundError message includes the framework name when provided."""
        exc = AdapterNotFoundError(framework_name="langgraph")
        assert "langgraph" in str(exc)

    def test_with_agent_type(self):
        """AdapterNotFoundError message includes the agent type when provided."""
        exc = AdapterNotFoundError(agent_type="CompiledStateGraph")
        assert "CompiledStateGraph" in str(exc)

    def test_with_no_args_fallback_message(self):
        """AdapterNotFoundError with no args produces a generic fallback message."""
        exc = AdapterNotFoundError()
        assert "No suitable adapter found" in str(exc)

    def test_framework_name_takes_precedence_over_agent_type(self):
        """When both framework_name and agent_type are given, only framework_name appears in message."""
        exc = AdapterNotFoundError(framework_name="langgraph", agent_type="Graph")
        assert "langgraph" in str(exc)
        assert "Graph" not in str(exc)


class TestUnsupportedOperationError:
    def test_message_contains_operation_and_framework(self):
        """UnsupportedOperationError message includes both operation and framework names."""
        exc = UnsupportedOperationError(operation="stream", framework="autogen")
        msg = str(exc)
        assert "stream" in msg
        assert "autogen" in msg

    def test_different_operations(self):
        """UnsupportedOperationError message includes the operation name for various operations."""
        for op in ("cancel", "get_state", "resume"):
            exc = UnsupportedOperationError(operation=op, framework="fw")
            assert op in str(exc)


class TestSimpleExceptions:
    @pytest.mark.parametrize("cls", [
        AdapterRegistrationError,
        ExecutionError,
        StateRetrievalError,
        MessageConversionError,
        ConfigurationError,
    ])
    def test_message_stored(self, cls):
        """Simple adapter exceptions preserve the message string passed at construction."""
        exc = cls("something went wrong")
        assert "something went wrong" in str(exc)

    @pytest.mark.parametrize("cls", [
        AdapterRegistrationError,
        ExecutionError,
        StateRetrievalError,
        MessageConversionError,
        ConfigurationError,
    ])
    def test_can_be_raised_and_caught(self, cls):
        """Simple adapter exceptions can be raised and caught by their own type."""
        with pytest.raises(cls):
            raise cls("error")
