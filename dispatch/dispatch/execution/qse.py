"""The QSE adapter interface (PRD FR9): it exists so the rest of the code has something to point at, and it stays disabled.

The PRD's first non-goal for v1 is "Acting as a QSE or submitting live bids to ERCOT". Execution therefore means the paper
ledger (execution/ledger.py). This module defines what a real adapter would have to provide and ships one implementation,
`DisabledQSEAdapter`, which refuses every call. `ENABLED` is False and `get_adapter()` will not hand out anything else.

Turning this on is not a config change. It needs a PRD change (live dispatch is a different product), a signed QSE
agreement, a real adapter class, and Gate 3 passing on paper first.
"""
from __future__ import annotations

from typing import Protocol

from ..contracts import Setpoint

ENABLED = False          # FR9: the interface exists but stays disabled in v1. Do not flip this to make a test pass.


class QSEDisabledError(RuntimeError):
    """Raised by every method of the disabled adapter."""


class QSEAdapter(Protocol):
    """What a live adapter would implement. Every call must be idempotent per `plan_id`."""

    def submit(self, setpoint: Setpoint) -> str:
        """Send the setpoint to the QSE. Returns the QSE's reference for it."""

    def cancel(self, reference: str) -> None:
        """Withdraw a submitted setpoint."""

    def status(self, reference: str) -> str:
        """The QSE's state for a submitted setpoint (accepted, rejected, dispatched, ...)."""


class DisabledQSEAdapter:
    """Refuses every call, so no code path can reach a market by accident."""

    enabled = False

    def _refuse(self, what: str):
        raise QSEDisabledError(f"QSE adapter is disabled in v1 (PRD FR9, non-goal 'live bids'): cannot {what}")

    def submit(self, setpoint: Setpoint) -> str:
        self._refuse("submit a setpoint")

    def cancel(self, reference: str) -> None:
        self._refuse("cancel a submission")

    def status(self, reference: str) -> str:
        self._refuse("read a submission status")


def get_adapter() -> QSEAdapter:
    """The only adapter v1 can have."""
    if ENABLED:
        raise RuntimeError("ENABLED must stay False in v1: there is no live QSE adapter")
    return DisabledQSEAdapter()
