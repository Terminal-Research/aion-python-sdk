"""`TaskSettlementReason.description` — the prose half of a settled state.

A settled task carries only its reason token in metadata; the sentence a
person reads is defined here, once, so every surface that renders one shows
the same wording. The property is therefore only useful if it holds for every
member — a reason added without one would fail at display time, on the very
path that exists to explain an unexplained failure.
"""

from __future__ import annotations

import pytest

from aion.core.a2a.enums import TaskSettlementReason


@pytest.mark.parametrize("reason", list(TaskSettlementReason))
def test_every_reason_describes_itself(reason: TaskSettlementReason):
    description = reason.description
    assert description.endswith("."), reason
    # Long enough to be a statement rather than a restated token.
    assert len(description.split()) >= 8, reason
    assert reason.value not in description, reason


def test_cancellation_reasons_read_as_cancelled_not_failed():
    """These two settle as CANCELED, and their wording has to agree with the
    state — telling someone their cancelled task "failed" is a false report."""
    for reason in (TaskSettlementReason.CANCEL_REQUESTED, TaskSettlementReason.CANCEL_TIMEOUT):
        assert "cancelled as requested" in reason.description
