"""Crash reconciliation at startup (the service's version of what resilience/ does when a worker dies mid-ramp).

A cycle writes its guard verdict to the audit log BEFORE it executes anything. If the process dies between that record and the
ledger write, the log holds an accepted decision that never became a paper setpoint. Nothing is wrong with the ledger (that
interval has no setpoint, so the asset idled, and the next cycle decides again), but the gap must be visible: at startup this
looks at the newest audit records, finds accepted decisions that no ledger line and no `setpoint` record answers, and writes
one `error` record per orphan (once; a decision already reported is not reported again).
"""
from __future__ import annotations

import logging

from ..execution.audit import AuditLog
from ..execution.ledger import LedgerError, PaperLedger

log = logging.getLogger("dispatch.service")

RECENT_RECORDS = 1000          # ASSUMPTION: a crash is looked for in the newest thousand audit records (about 250 cycles)
WHERE = "startup.reconcile"


def unexecuted_decisions(audit: AuditLog, ledger: PaperLedger, *, limit: int = RECENT_RECORDS) -> list:
    """Accepted guard decisions among the newest `limit` audit records that were never executed and not yet reported."""
    recs = audit.recent(limit)
    executed = {r.get("guard_audit_hash") for r in recs if r["type"] == "setpoint"}
    reported = {r.get("guard_decision_hash") for r in recs if r["type"] == "error" and r.get("where") == WHERE}
    orphans = []
    for r in recs:
        if r["type"] != "guard_decision" or not r.get("accepted") or r["hash"] in executed or r["hash"] in reported:
            continue
        asset = r.get("asset_id")
        try:
            if asset and ledger.has_asset(asset) and any(e.audit_hash == r["hash"] for e in ledger.entries(asset, final_only=False)):
                continue                                    # executed; the crash was after the ledger write, before its audit record
        except LedgerError:
            pass
        orphans.append({"asset_id": asset, "guard_decision_hash": r["hash"], "decision_seq": r["seq"], "plan_id": r.get("plan_id"),
                        "decided_utc": r.get("ts_utc")})
    return orphans


def reconcile(audit: AuditLog, ledger: PaperLedger, *, limit: int = RECENT_RECORDS) -> list:
    """Find the orphans and record each one in the audit log. Returns them. Never raises."""
    try:
        orphans = unexecuted_decisions(audit, ledger, limit=limit)
        for o in orphans:
            log.warning("accepted decision %s for %s was never executed (a crash between the audit write and the ledger write?)",
                        o["guard_decision_hash"][:12], o["asset_id"])
            audit.append({"type": "error", "asset_id": o["asset_id"], "where": WHERE,
                          "error": "an accepted guard decision was never executed", **o})
        return orphans
    except Exception as exc:
        log.error("reconcile failed: %s", exc)
        return []
