"""What repeated monitoring breaches do (PRD FR11: "repeated breaches set the halt flag automatically").

`HaltPolicy.evaluate(breaches, checks_run)` is called once per monitoring pass. For every check that ran:
  - it breached: its consecutive count goes up by one, a `breach` audit record is written, and the metric moves;
  - it was clean: its count goes back to zero.
When a check reaches its own threshold of consecutive breaching passes, the policy sets the halt flag (with the reason and
`set_by="monitor:halt_policy"`) and writes a `halt` audit record. The guard then rejects every setpoint until an operator calls
POST /resume. The flag is set BEFORE the audit record is attempted: safety first, paperwork second.

Thresholds (DESIGN.md "Monitoring policy"). Stale data, capture decay and slippage halt after `HALT_AFTER_BREACHES` (3) consecutive
passes. The calibration check runs once a day, so its passes are days: it halts after `CALIBRATION_HALT_AFTER` (2) consecutive daily
breaches, and one breach is an alert plus a retrain request.

Alert-only breaches. A `Breach` with `halts=False` (feature PSI, a stale report that is only a diagnostic, the pinball rule of the
calibration check) is shown in the metrics and written to the audit log (the first of a streak, then rarely), and it NEVER counts: a pass
that has only alert-only breaches leaves its check at zero. That is what "alert only" means here, and it is why PSI, which flags nearly
every feature on in-distribution data (D1a, notes/d1a.md section 7.1), cannot halt dispatch within minutes.

Counting is per check, because the checks run on different clocks (feed freshness every pass, capture rate and slippage over days,
calibration once a day). Resuming does not reset the counts: if the cause is still there, the next time that check RUNS and breaches it
halts again, which is the point (a minute later for the per-pass checks, at the next daily evaluation for calibration; a check that did
not run in a pass cannot halt in it). Fix the cause (or promote a model whose forecasts are calibrated and restart, which starts the
calibration streak afresh), then resume.

Retrain trigger. A calibration or capture-decay breach is the kind of problem a retrain can fix, so the first breach of such a streak
calls `request_retrain`, which ONLY RECORDS the request (audit record `retrain_request`, the in-memory list, and an optional callback).
It does not train, schedule or promote anything. Stale data and slippage are not retrain problems, and feature PSI no longer asks for
one: it fires on nearly every window, so a request per streak would only be noise.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

import pandas as pd

from ..config import CALIBRATION_HALT_AFTER, HALT_AFTER_BREACHES
from ..contracts import HaltFlag
from ..execution.audit import AuditError, AuditLog
from .checks import CHECK_CALIBRATION, CHECK_CAPTURE, CHECKS, Breach, check_halts
from .metrics import Metrics

log = logging.getLogger("dispatch.monitoring")

RETRAIN_CHECKS = (CHECK_CALIBRATION, CHECK_CAPTURE)    # breaches a retrain could fix
BREACH_AUDIT_EVERY = 30                                # ASSUMPTION: while a check keeps breaching, every pass up to the halt is audited, then every 30th
ALERT_AUDIT_EVERY = 360                                # ASSUMPTION: an alert-only streak is audited when it starts and then every 360th pass (6 h at 60 s)
DEFAULT_THRESHOLDS = {CHECK_CALIBRATION: CALIBRATION_HALT_AFTER}     # every other check uses HALT_AFTER_BREACHES
SET_BY = "monitor:halt_policy"


@dataclass(frozen=True)
class RetrainRequest:
    requested_utc: pd.Timestamp
    reason: str
    checks: tuple
    breaches: tuple


@dataclass
class PolicyOutcome:
    halted_now: bool = False
    tripped: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    halt_reason: str = ""
    retrain_requested: bool = False
    alerts: list = field(default_factory=list)         # checks that had alert-only breaches this pass


class HaltPolicy:
    def __init__(self, halt: HaltFlag, audit: AuditLog, *, threshold: Optional[int] = None,
                 thresholds: Optional[Mapping[str, int]] = None, metrics: Optional[Metrics] = None,
                 on_retrain: Optional[Callable[[RetrainRequest], None]] = None, retrain_checks: Iterable[str] = RETRAIN_CHECKS,
                 set_by: str = SET_BY, clock: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC")):
        """`threshold` is the number of consecutive breaching passes that halts, for every check (default HALT_AFTER_BREACHES, with
        the calibration check at CALIBRATION_HALT_AFTER). `thresholds` overrides single checks."""
        if threshold is not None and threshold < 1:
            raise ValueError("threshold must be at least 1")
        self.halt, self.audit, self.metrics = halt, audit, metrics
        self.threshold = int(HALT_AFTER_BREACHES if threshold is None else threshold)
        self.thresholds = {c: self.threshold for c in CHECKS}
        if threshold is None:
            self.thresholds.update(DEFAULT_THRESHOLDS)
        for c, n in (thresholds or {}).items():
            if int(n) < 1:
                raise ValueError(f"threshold of {c} must be at least 1")
            self.thresholds[c] = int(n)
        self.on_retrain, self.retrain_checks, self.set_by, self._clock = on_retrain, tuple(retrain_checks), set_by, clock
        self.counts: dict = {c: 0 for c in CHECKS}
        self.alert_streaks: dict = {c: 0 for c in CHECKS}
        self.retrain_requests: list = []
        self.last_outcome: Optional[PolicyOutcome] = None
        self._lock = threading.RLock()
        if self.metrics is not None:
            for c, n in self.thresholds.items():
                self.metrics.set_halt_threshold(c, n)

    def threshold_of(self, check: str) -> int:
        return self.thresholds.get(check, self.threshold)

    # -- the pass
    def evaluate(self, breaches: Iterable[Breach], checks_run: Optional[Iterable[str]] = None) -> PolicyOutcome:
        breaches = list(breaches)
        by_check: dict = defaultdict(list)
        for b in breaches:
            by_check[b.check].append(b)
        run = list(checks_run) if checks_run is not None else list(by_check)
        out = PolicyOutcome()
        with self._lock:
            for check in run:
                found = by_check.get(check, [])
                can_halt = check_halts(check)                       # feature PSI cannot, whatever the Breach says (defence in depth)
                halting = [b for b in found if b.halts and can_halt]
                alerts = [b for b in found if not (b.halts and can_halt)]
                if halting:
                    self.counts[check] = self.counts.get(check, 0) + 1
                    self._record_breach(check, halting, self.counts[check])
                    if check in self.retrain_checks and self.counts[check] == 1:
                        self.request_retrain(f"{check} breached", halting)
                        out.retrain_requested = True
                else:
                    self.counts[check] = 0
                if alerts:
                    self._record_alert(check, alerts)
                    out.alerts.append(check)
                else:
                    self.alert_streaks[check] = 0
                if self.metrics is not None:
                    self.metrics.set_consecutive(check, self.counts[check])
            out.counts = dict(self.counts)
            # only a check that RAN in this pass can halt: the daily calibration check is skipped on the 1,439 other passes of the day, and a halt
            # it caused must stay cleared by POST /resume until its next evaluation, not be set again a minute later by a count it left behind
            out.tripped = [c for c in run if self.counts.get(c, 0) >= self.threshold_of(c)]
            if out.tripped:
                out = self._halt(out, by_check)
            self.last_outcome = out
        return out

    def restore(self, counts: Mapping[str, int]) -> None:
        """Set consecutive-breach counts that outlive the process (the calibration check's streak, kept in its state file): a restart must
        neither forget a breach nor make one up."""
        with self._lock:
            for check, n in counts.items():
                self.counts[check] = max(int(n), 0)
                if self.metrics is not None:
                    self.metrics.set_consecutive(check, self.counts[check])

    def _record_breach(self, check: str, breaches: list, count: int) -> None:
        threshold = self.threshold_of(check)
        if self.metrics is not None:
            self.metrics.breach(check)
        if count > threshold and count % BREACH_AUDIT_EVERY:               # a halted service must not fill the log once a minute
            return
        self._audit({"type": "breach", "check": check, "consecutive": count, "threshold": threshold, "halts": True,
                     "breaches": [_brief(b) for b in breaches][:20]})

    def _record_alert(self, check: str, alerts: list) -> None:
        self.alert_streaks[check] = self.alert_streaks.get(check, 0) + 1
        n = self.alert_streaks[check]
        if self.metrics is not None:
            self.metrics.breach(check)
        if n == 1 or n % ALERT_AUDIT_EVERY == 0:
            self._audit({"type": "breach", "check": check, "consecutive": 0, "alert_pass": n, "halts": False,
                         "breaches": [_brief(b) for b in alerts][:20]})

    def _halt(self, out: PolicyOutcome, by_check: dict) -> PolicyOutcome:
        try:
            already, _ = self.halt.is_set()
        except Exception:                                   # a halt flag that cannot be read is treated as not yet set
            already = False
        if already:
            return out
        detail = "; ".join(f"{c} x{self.counts[c]}: " + (_first_halting(by_check.get(c, []))) for c in out.tripped)
        reason = f"auto-halt after {self.threshold_of(out.tripped[0])} consecutive breaching checks: {detail}"[:480]
        try:
            self.halt.set(reason, self.set_by)              # first: nothing dispatches from here on
        except Exception as exc:
            log.error("auto-halt could not set the halt flag: %s", exc)
            self._audit({"type": "error", "where": "halt_policy.set", "error": f"{type(exc).__name__}: {exc}"})
            return out
        if self.metrics is not None:
            self.metrics.set_halted(True)
        self._audit({"type": "halt", "source": "monitor", "reason": reason, "set_by": self.set_by,
                     "checks": list(out.tripped), "counts": dict(self.counts),
                     "thresholds": {c: self.threshold_of(c) for c in out.tripped},
                     "breaches": [_brief(b) for c in out.tripped for b in by_check.get(c, []) if b.halts][:20]})
        out.halted_now, out.halt_reason = True, reason
        return out

    # -- retrain hook
    def request_retrain(self, reason: str, breaches: Iterable[Breach] = ()) -> RetrainRequest:
        """Record that monitoring wants a retrain. Nothing is trained, scheduled or promoted here."""
        breaches = tuple(breaches)
        req = RetrainRequest(self._clock(), reason, tuple(sorted({b.check for b in breaches})), breaches)
        with self._lock:
            self.retrain_requests.append(req)
        self._audit({"type": "retrain_request", "reason": reason, "checks": list(req.checks),
                     "breaches": [_brief(b) for b in req.breaches][:20], "trains": False})
        if self.on_retrain is not None:
            try:
                self.on_retrain(req)
            except Exception as exc:                        # a hook never stops the monitor
                log.warning("retrain hook failed: %s", exc)
        return req

    # -- housekeeping
    def reset(self, check: Optional[str] = None) -> None:
        with self._lock:
            for c in ([check] if check else list(self.counts)):
                self.counts[c] = 0
                if self.metrics is not None:
                    self.metrics.set_consecutive(c, 0)

    def status(self) -> dict:
        with self._lock:
            return {"threshold": self.threshold, "thresholds": dict(self.thresholds), "consecutive": dict(self.counts),
                    "alert_streaks": {c: n for c, n in self.alert_streaks.items() if n},
                    "retrain_requests": len(self.retrain_requests),
                    "last_halt_reason": self.last_outcome.halt_reason if self.last_outcome else ""}

    def _audit(self, event: dict) -> None:
        try:
            self.audit.append(event)
        except (AuditError, ValueError, OSError) as exc:
            log.error("audit append failed in halt policy: %s", exc)
            if self.metrics is not None:
                self.metrics.audit_error()


def _first_halting(breaches: list) -> str:
    for b in breaches:
        if b.halts:
            return b.message
    return ""


def _brief(b: Breach) -> dict:
    return {"check": b.check, "subject": b.subject, "value": b.value, "threshold": b.threshold, "message": b.message,
            "severity": b.severity, "halts": b.halts}
