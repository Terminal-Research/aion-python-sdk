"""The agent's shutdown budget, ``AION_SHUTDOWN_TIMEOUT``, and how it is divided among the steps of a shutdown."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aion.server import shutdown
from aion.server.settings import AppSettings, app_settings
from aion.server.shutdown import ShutdownBudget, shutdown_budget


def test_the_default_budget_is_fifteen_five_two_and_three_seconds() -> None:
    budget = shutdown_budget()

    assert (budget.total, budget.turns, budget.graceful, budget.settlement, budget.cleanup) == (
        25.0,
        15.0,
        5.0,
        2.0,
        3.0,
    )
    assert not budget.too_short


def test_a_larger_budget_goes_to_the_extensions_turns() -> None:
    budget = ShutdownBudget.of(60)

    assert (budget.graceful, budget.settlement, budget.cleanup) == (5.0, 2.0, 3.0)
    assert budget.turns == 50.0


@pytest.mark.parametrize("total", [2.0, 5.0, 10.0, 20.0])
def test_a_small_budget_shrinks_the_fixed_steps_and_adds_up(total: float) -> None:
    budget = ShutdownBudget.of(total)

    assert budget.graceful <= 0.2 * total
    assert budget.turns >= 1.0
    assert budget.turns + budget.graceful + budget.settlement + budget.cleanup == pytest.approx(total)


def test_the_extensions_turns_get_at_least_a_second() -> None:
    budget = ShutdownBudget.of(0.5)

    assert budget.turns == 1.0
    assert budget.too_short


def test_a_budget_below_the_sane_minimum_is_too_short() -> None:
    assert ShutdownBudget.of(shutdown.MIN_SANE_SHUTDOWN_SECONDS - 1).too_short
    assert not ShutdownBudget.of(shutdown.MIN_SANE_SHUTDOWN_SECONDS).too_short


def test_the_budget_is_read_from_the_setting(monkeypatch) -> None:
    monkeypatch.setattr(app_settings, "shutdown_timeout", 5.0)

    assert shutdown_budget() == ShutdownBudget.of(5.0)
    assert shutdown_budget(8.0) == ShutdownBudget.of(8.0)


def test_the_setting_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("AION_SHUTDOWN_TIMEOUT", "7.5")

    assert AppSettings().shutdown_timeout == 7.5


@pytest.mark.parametrize("value", ["0", "-3", "soon", "nan", "inf"])
def test_a_budget_that_is_not_a_positive_number_stops_the_startup(monkeypatch, value: str) -> None:
    monkeypatch.setenv("AION_SHUTDOWN_TIMEOUT", value)

    with pytest.raises(ValidationError, match="AION_SHUTDOWN_TIMEOUT"):
        AppSettings()
