"""The audit log (FR10), the paper ledger (FR9) and the disabled QSE adapter (FR9).

Audit: hash chain, tamper detection, append-only behaviour. Ledger: SOC tracking, refusals, settlement through
dispatch.units, replay. QSE: refuses everything. (The ledger and the QSE adapter are tested here because this file and
test_api.py, test_monitoring.py, test_guard_props.py and fakes.py are the only test files D2 owns.)
"""
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from dispatch import units
from dispatch.config import ERCOT_PRICE_CAP
from dispatch.contracts import BatterySpec, Setpoint, as_price_col
from dispatch.execution import audit as A
from dispatch.execution import qse
from dispatch.execution.audit import AuditError, AuditLog, GENESIS_HASH
from dispatch.execution.ledger import LedgerError, PaperLedger, reference_transition
from dispatch.guard.guard import project_soc_end

T0 = datetime(2026, 10, 10, 15, 7, 30, tzinfo=timezone.utc)


def stepping_clock(start=T0, step=timedelta(seconds=1)):
    state = {"t": start}

    def clock():
        state["t"] = state["t"] + step
        return state["t"]
    return clock


@pytest.fixture()
def log(tmp_path):
    return AuditLog(tmp_path / "audit", clock=stepping_clock())


def lines(log):
    return [ln for f in log.files() for ln in f.read_bytes().split(b"\n")[:-1]]


def fill(log, n=5):
    return [log.append({"type": "signal", "asset_id": "A1", "n": i, "note": f"event {i}"}) for i in range(n)]


# ================================================================================================ audit: the chain
def test_each_record_carries_prev_hash_and_its_own_hash(log):
    hashes = fill(log, 4)
    recs = [json.loads(x) for x in lines(log)]
    prev = GENESIS_HASH
    for i, rec in enumerate(recs):
        assert rec["seq"] == i and rec["prev_hash"] == prev and rec["hash"] == hashes[i]
        body = {k: v for k, v in rec.items() if k != "hash"}
        # the documented algorithm, recomputed here without dispatch.execution.audit
        again = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        assert again == rec["hash"]
        prev = rec["hash"]
    assert log.verify() == (True, None)
    assert log.head() == (3, hashes[-1])


def test_an_empty_log_verifies_and_has_the_genesis_head(log):
    assert log.verify() == (True, None) and log.head() == (-1, GENESIS_HASH)


def test_record_fields(log):
    log.append({"type": "kill", "reason": "test", "set_by": "ops"})
    rec = json.loads(lines(log)[0])
    assert rec["schema"] == 1 and rec["type"] == "kill" and rec["ts_utc"].endswith("Z") and rec["reason"] == "test"


def test_only_known_event_types_and_no_reserved_fields(log):
    with pytest.raises(ValueError):
        log.append({"type": "made_up"})
    with pytest.raises(ValueError):
        log.append({"note": "no type"})
    with pytest.raises(ValueError):
        log.append({"type": "signal", "hash": "forged"})
    with pytest.raises(ValueError):
        log.append({"type": "signal", "seq": 99})
    assert log.verify() == (True, None) and log.head()[0] == -1          # nothing was written


def test_the_events_the_prd_lists_are_all_accepted(log):
    for t in ("signal", "guard_decision", "setpoint", "kill", "resume", "halt", "error", "model_load"):
        log.append({"type": t})
    assert log.verify() == (True, None)


def test_values_that_are_not_json_are_converted_and_nan_stays_visible(log):
    sp = Setpoint("A1", pd.Timestamp("2026-10-10 15:15", tz="UTC"), float("nan"), {"REGUP": np.float64(1.5)}, "p", "v1",
                  pd.Timestamp("2026-10-10 15:07:30", tz="UTC"))
    log.append({"type": "guard_decision", "setpoint": sp, "n": np.int64(3), "inf": float("inf"), "when": pd.NaT,
                "arr": np.array([1.0, 2.0]), "path": A.Path("C:/x/y")})
    raw = lines(log)[0]

    def refuse(token):
        raise AssertionError(f"line is not strict JSON: {token}")
    rec = json.loads(raw, parse_constant=refuse)
    assert rec["setpoint"]["net_mw"] == "NaN" and rec["inf"] == "Infinity" and rec["n"] == 3
    assert rec["setpoint"]["interval_start_utc"] == "2026-10-10T15:15:00Z" and rec["when"] is None
    assert rec["setpoint"]["as_mw"] == {"REGUP": 1.5} and rec["arr"] == [1.0, 2.0]
    assert log.verify() == (True, None)


def test_a_huge_record_is_replaced_by_its_size_and_hash_and_the_chain_holds(log):
    log.append({"type": "signal", "asset_id": "A1", "blob": "x" * (A.MAX_RECORD_BYTES + 10)})
    log.append({"type": "signal"})
    rec = json.loads(lines(log)[0])
    assert rec["truncated"] is True and rec["original_bytes"] > A.MAX_RECORD_BYTES and len(rec["original_sha256"]) == 64
    assert log.verify() == (True, None)


def test_reopening_continues_the_same_chain(tmp_path):
    d = tmp_path / "audit"
    first = AuditLog(d, clock=stepping_clock())
    h = fill(first, 3)
    second = AuditLog(d, clock=stepping_clock(T0 + timedelta(hours=1)))
    assert second.head() == (2, h[-1])
    second.append({"type": "resume"})
    assert second.verify() == (True, None) and first.head()[0] == 3         # first notices the other writer's record


def test_two_objects_on_one_directory_do_not_fork_the_chain(tmp_path):
    d = tmp_path / "audit"
    a, b = AuditLog(d, clock=stepping_clock()), AuditLog(d, clock=stepping_clock())
    for i in range(10):
        (a if i % 2 else b).append({"type": "signal", "n": i})
    assert a.verify() == (True, None) and a.head()[0] == 9


def test_threads_append_without_losing_or_forking(tmp_path):
    log = AuditLog(tmp_path / "audit", fsync=False)

    def work(k):
        for i in range(40):
            log.append({"type": "signal", "worker": k, "i": i})

    ts = [threading.Thread(target=work, args=(k,)) for k in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rep = log.verify_report()
    assert rep.ok and rep.records == 320 and rep.head_seq == 319


def test_the_chain_runs_across_daily_files(tmp_path):
    clock = stepping_clock(datetime(2026, 10, 10, 23, 59, 50, tzinfo=timezone.utc), timedelta(seconds=4))
    log = AuditLog(tmp_path / "audit", clock=clock)
    fill(log, 6)                                                           # 23:59:54 ... 00:00:14: crosses midnight UTC
    names = [f.name for f in log.files()]
    assert names == ["audit-20261010.jsonl", "audit-20261011.jsonl"]
    assert log.verify() == (True, None)
    # losing a whole day is a broken chain, not a smaller log
    log.files()[0].unlink()
    ok, bad = log.verify()
    assert not ok and bad == 1


def test_a_clock_that_steps_back_keeps_writing_to_the_newest_file(tmp_path):
    times = iter([datetime(2026, 10, 11, 0, 0, 1, tzinfo=timezone.utc), datetime(2026, 10, 10, 23, 0, 0, tzinfo=timezone.utc)])
    log = AuditLog(tmp_path / "audit", clock=lambda: next(times))
    log.append({"type": "signal"})
    log.append({"type": "signal"})
    assert [f.name for f in log.files()] == ["audit-20261011.jsonl"] and log.verify() == (True, None)


def test_recent_reads_the_end_of_the_log_across_files(tmp_path):
    clock = stepping_clock(datetime(2026, 10, 10, 23, 59, 50, tzinfo=timezone.utc), timedelta(seconds=4))
    log = AuditLog(tmp_path / "audit", clock=clock, fsync=False)
    for i in range(300):
        log.append({"type": "signal", "n": i, "pad": "x" * 200})
    assert len(log.files()) > 1
    last = log.recent(10)
    assert [r["n"] for r in last] == list(range(290, 300)) and [r["seq"] for r in last] == list(range(290, 300))
    assert [r["n"] for r in log.recent(2000)] == list(range(300)), "asking for more than exists returns everything"
    assert AuditLog(tmp_path / "empty").recent(5) == []


def test_retention_is_a_named_open_question_and_nothing_expires(tmp_path):
    assert A.AUDIT_RETENTION_DAYS is None                                   # PRD open question: undecided
    log = AuditLog(tmp_path / "audit", clock=stepping_clock(datetime(2020, 1, 1, tzinfo=timezone.utc), timedelta(days=400)))
    fill(log, 5)                                                            # records 1,600 days apart: all are kept
    assert len(log.files()) == 5 and log.verify() == (True, None)


# ================================================================================================ audit: tamper detection
def rewrite(log, n, fn):
    """Apply fn(list_of_lines) -> list_of_lines to the only file of `log`."""
    f = log.files()[0]
    ls = f.read_bytes().split(b"\n")[:-1]
    f.write_bytes(b"\n".join(fn(ls)) + b"\n")


def edited(line: bytes, **changes) -> bytes:
    rec = json.loads(line)
    rec.update(changes)
    return json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()


def test_editing_a_record_is_detected_at_that_line(log):
    fill(log, 6)
    rewrite(log, 0, lambda ls: ls[:2] + [edited(ls[2], note="altered")] + ls[3:])
    assert log.verify() == (False, 3)


def test_editing_a_record_and_fixing_its_hash_is_detected_at_the_next_line(log):
    fill(log, 6)

    def fix(ls):
        rec = json.loads(ls[2])
        rec["note"] = "altered"
        rec["hash"] = A.record_hash(rec)
        return ls[:2] + [json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()] + ls[3:]
    rewrite(log, 0, fix)
    assert log.verify() == (False, 4)


def test_deleting_a_record_is_detected(log):
    fill(log, 6)
    rewrite(log, 0, lambda ls: ls[:2] + ls[3:])
    assert log.verify() == (False, 3)


def test_inserting_a_record_is_detected(log):
    fill(log, 4)
    forged = json.loads(lines(log)[1])
    forged["note"] = "forged"
    rewrite(log, 0, lambda ls: ls[:2] + [json.dumps(forged, sort_keys=True, separators=(",", ":")).encode()] + ls[2:])
    ok, bad = log.verify()
    assert not ok and bad == 3


def test_reordering_records_is_detected(log):
    fill(log, 5)
    rewrite(log, 0, lambda ls: [ls[0], ls[2], ls[1]] + ls[3:])
    assert log.verify() == (False, 2)


def test_a_blank_or_garbage_line_is_detected(log):
    fill(log, 3)
    rewrite(log, 0, lambda ls: ls[:1] + [b""] + ls[1:])
    assert log.verify() == (False, 2)


def test_a_torn_last_line_is_detected_and_appends_refuse(log):
    fill(log, 3)
    f = log.files()[0]
    f.write_bytes(f.read_bytes()[:-20])                                    # a crash in the middle of the last write
    ok, bad = log.verify()
    assert not ok and bad == 3
    fresh = AuditLog(log.dir)
    before = f.read_bytes()
    with pytest.raises(AuditError):
        fresh.append({"type": "signal"})                                   # no audit record means no dispatch
    assert f.read_bytes() == before


def test_truncating_the_tail_is_invisible_to_the_chain_but_not_to_an_anchor(log):
    hashes = fill(log, 6)
    anchor = log.head()
    assert anchor == (5, hashes[-1])
    rewrite(log, 0, lambda ls: ls[:4])                                     # the last two records are gone
    assert log.verify() == (True, None)                                    # a bare hash chain cannot see it
    ok, bad = log.verify(expected_head=anchor)
    assert not ok and bad is not None
    assert log.verify(expected_head=(3, hashes[3])) == (True, None)        # an older anchor is still consistent


def test_an_anchor_that_disagrees_with_the_log_is_detected(log):
    hashes = fill(log, 4)
    assert not log.verify(expected_head=(2, "0" * 64))[0]
    assert log.verify(expected_head=(2, hashes[2])) == (True, None)


def test_the_anchor_hook_sees_every_append(tmp_path):
    seen = []
    log = AuditLog(tmp_path / "audit", anchor=lambda s, h: seen.append((s, h)))
    h = fill(log, 3)
    assert seen == [(0, h[0]), (1, h[1]), (2, h[2])]
    broken = AuditLog(tmp_path / "audit2", anchor=lambda s, h: 1 / 0)
    broken.append({"type": "signal"})                                      # a failing anchor never blocks the log
    assert broken.verify() == (True, None)


@settings(max_examples=150, deadline=None, database=None, suppress_health_check=list(HealthCheck))
@given(events=st.lists(st.fixed_dictionaries({"n": st.integers(0, 10**6), "s": st.text("abcdef ghij", max_size=12),
                                              "f": st.floats(0, 1e6, allow_nan=False)}), min_size=1, max_size=6),
       pos=st.integers(0, 10**6), byte=st.integers(0, 255))
def test_any_change_to_the_content_of_the_file_is_detected(events, pos, byte, tmp_path_factory):
    log = AuditLog(tmp_path_factory.mktemp("a") / "audit", clock=stepping_clock())
    for ev in events:
        log.append({"type": "signal", **ev})
    f = log.files()[0]
    data = bytearray(f.read_bytes())
    before = [json.loads(x) for x in bytes(data).split(b"\n")[:-1]]
    i = pos % len(data)
    if data[i] == byte:
        return
    data[i] = byte
    f.write_bytes(bytes(data))
    try:
        after = [json.loads(x) for x in bytes(data).split(b"\n")[:-1]]
    except (ValueError, UnicodeDecodeError):
        after = None
    if after == before:
        return                                                             # e.g. 1e-06 -> 1E-06: same content, nothing to detect
    assert log.verify()[0] is False


def test_a_tampered_number_that_overflows_is_reported_as_an_edited_record_not_raised(tmp_path):
    """Found by the property test above (I1, 10 Oct 2026): changing one digit of 262144.9998005644 to `e` gives 262144.9998e05644, which parses
    to inf; the canonical hash refuses inf, so `verify` used to raise ValueError (a 500 from /resume) instead of saying where the log is broken."""
    log = AuditLog(tmp_path / "audit", clock=stepping_clock())
    log.append({"type": "signal", "f": 262144.9998005644})
    log.append({"type": "signal", "f": 1.5})
    f = log.files()[0]
    raw = f.read_bytes()
    assert b"262144.9998005644" in raw
    f.write_bytes(raw.replace(b"262144.9998005644", b"262144.9998e05644"))
    rep = log.verify_report()
    assert rep.ok is False and rep.first_bad_line == 1 and "cannot be hashed" in rep.reason
    assert log.verify() == (False, 1)


def test_append_only_the_earlier_bytes_never_change_and_there_is_no_way_to_delete(log):
    fill(log, 3)
    before = log.files()[0].read_bytes()
    fill(log, 4)
    after = log.files()[0].read_bytes()
    assert after.startswith(before) and len(after) > len(before)
    public = {n for n in dir(AuditLog) if not n.startswith("_")}
    assert not public & {"delete", "remove", "update", "edit", "rewrite", "truncate", "clear", "rotate", "purge"}


def test_a_failed_write_raises_and_the_chain_stays_valid(tmp_path, monkeypatch):
    log = AuditLog(tmp_path / "audit")
    fill(log, 2)
    before = log.files()[0].read_bytes()
    monkeypatch.setattr(A.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(AuditError):
        log.append({"type": "signal"})
    monkeypatch.undo()
    assert log.files()[0].read_bytes().startswith(before)                  # nothing earlier was touched
    log.append({"type": "signal"})                                         # recovers: the tail is re-read first
    assert log.verify() == (True, None)                                    # whether or not the failed line reached the disk


# ================================================================================================ ledger
SPEC = BatterySpec("A1", "HB_NORTH", power_mw=10.0, energy_mwh=20.0, rte=0.85, loss_side="charge", wear_usd_per_mwh=20.0)
I0 = pd.Timestamp("2026-10-10 15:00", tz="UTC")
STEP = pd.Timedelta(minutes=15)


def sp(k, net, as_mw=None, issued_extra=0, plan="p", spec=SPEC):
    return Setpoint(spec.asset_id, I0 + STEP * k, net, as_mw or {}, plan, "v1",
                    I0 + STEP * k + pd.Timedelta(seconds=1 + issued_extra))


@pytest.fixture()
def ledger(tmp_path):
    led = PaperLedger(tmp_path / "ledger", clock=lambda: pd.Timestamp("2026-10-10 16:00", tz="UTC"), fsync=False)
    led.init_asset(SPEC, 10.0)
    return led


def prices(n, rt=100.0, **extra):
    idx = pd.date_range(I0, periods=n, freq="15min", name="interval_start_utc")
    df = pd.DataFrame({"rt_usd_per_mwh": np.broadcast_to(rt, n).astype(float)}, index=idx)
    for k, v in extra.items():
        df[k] = v
    return df


def test_init_is_idempotent_and_the_file_is_jsonl(ledger):
    assert ledger.init_asset(SPEC, 3.0) is False                           # already there: untouched
    assert ledger.soc_at("A1", I0) == 10.0
    first = ledger.path("A1").read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(first)["type"] == "init"


def test_paper_soc_follows_dispatch_with_the_guards_own_function(ledger):
    e1 = ledger.record(SPEC, sp(0, -8.0))                                  # charge 8 MW: +0.85 * 8 * 0.25 = +1.7 MWh
    assert e1.soc_start_mwh == 10.0 and e1.soc_end_mwh == pytest.approx(11.7)
    assert e1.soc_end_mwh == pytest.approx(project_soc_end(SPEC, 10.0, -8.0))
    e2 = ledger.record(SPEC, sp(1, +6.0))                                  # discharge 6 MW: -1.5 MWh
    assert e2.soc_start_mwh == pytest.approx(11.7) and e2.soc_end_mwh == pytest.approx(10.2)
    assert ledger.soc_at("A1", I0 + 2 * STEP) == pytest.approx(10.2)       # the SOC the next cycle starts from
    assert ledger.soc_at("A1", I0 + STEP) == pytest.approx(11.7)
    assert ledger.soc_at("A1", I0) == 10.0
    assert ledger.soc_reading("A1", I0 + 2 * STEP) == (pytest.approx(10.2), 0.0)
    assert ledger.soc_at("A1", I0 + 10 * STEP) == pytest.approx(10.2)      # idle where nothing was recorded


def test_the_file_is_append_only_and_a_new_instance_sees_the_same_state(ledger, tmp_path):
    ledger.record(SPEC, sp(0, -4.0))
    before = ledger.path("A1").read_bytes()
    ledger.record(SPEC, sp(1, 2.0))
    assert ledger.path("A1").read_bytes().startswith(before)
    again = PaperLedger(ledger.dir)
    assert again.soc_at("A1", I0 + 2 * STEP) == pytest.approx(ledger.soc_at("A1", I0 + 2 * STEP))
    assert [e.seq for e in again.entries("A1")] == [0, 1]


def test_a_later_setpoint_for_the_same_interval_supersedes_the_earlier_one(ledger):
    ledger.record(SPEC, sp(0, -8.0))
    ledger.record(SPEC, sp(0, +4.0, issued_extra=300, plan="p2"))          # the 15:05 cycle changes its mind
    assert len(ledger.entries("A1", final_only=False)) == 2                # both lines are in the file
    final = ledger.entries("A1")
    assert len(final) == 1 and final[0].net_mw == 4.0 and final[0].soc_start_mwh == 10.0
    assert ledger.soc_at("A1", I0 + STEP) == pytest.approx(9.0)            # 10 - 4 * 0.25
    assert ledger.revenue_usd(SPEC, prices(1)) == pytest.approx(100.0 * 4.0 * 0.25 - 20.0 * 4.0 * 0.25)


def test_refusals_write_nothing(ledger):
    ledger.record(SPEC, sp(1, -2.0))
    size = ledger.path("A1").stat().st_size

    def refused(setpoint, code):
        with pytest.raises(LedgerError) as exc:
            ledger.record(SPEC, setpoint)
        assert exc.value.code.startswith(code), exc.value.code
        assert ledger.path("A1").stat().st_size == size

    refused(sp(0, -2.0), "stale_interval")                                 # an interval older than the newest
    refused(sp(1, -3.0, issued_extra=-1, plan="q"), "non_increasing_issue")   # issued no later than the last one
    refused(Setpoint("B2", I0 + 2 * STEP, 0.0, {}, "p", "v1", I0 + 2 * STEP), "asset_mismatch")
    refused(Setpoint("A1", I0 + 2 * STEP + pd.Timedelta(seconds=5), 0.0, {}, "p", "v1", I0 + 3 * STEP), "bad_interval")
    refused(Setpoint("A1", (I0 + 2 * STEP).tz_localize(None), 0.0, {}, "p", "v1", I0 + 3 * STEP), "bad_interval")
    refused(sp(2, 10.5, issued_extra=9), "limit_violation:power")          # the guard should have caught these
    refused(sp(2, 0.0, {"REGUP": -3.0}, issued_extra=9), "limit_violation:as_negative")
    refused(sp(2, 0.0, {"FOO": 1.0}, issued_extra=9), "limit_violation:as_product")
    refused(sp(2, float("nan"), issued_extra=9), "limit_violation:non_finite")


def test_the_ledger_refuses_what_the_guard_should_have_stopped(ledger):
    """Defence in depth: the SOC the ledger holds, not the SOC the caller says, decides."""
    tight = BatterySpec("A1", "HB_NORTH", power_mw=10.0, energy_mwh=20.0, soc_max_mwh=10.5, rte=0.85)
    with pytest.raises(LedgerError) as exc:
        ledger.record(tight, sp(0, -10.0, spec=tight))                      # 10 + 2.125 > 10.5
    assert exc.value.code == "limit_violation:soc"


def test_resubmitting_an_identical_setpoint_is_a_no_op(ledger):
    a = ledger.record(SPEC, sp(0, -4.0))
    n = len(ledger.path("A1").read_bytes().splitlines())
    b = ledger.record(SPEC, sp(0, -4.0))
    assert a == b and len(ledger.path("A1").read_bytes().splitlines()) == n


def test_asset_ids_cannot_escape_the_directory(ledger):
    for bad in ("../x", "a/b", "", "a b", "x" * 80):
        with pytest.raises(LedgerError):
            ledger.path(bad)


def test_an_unknown_asset_has_no_soc(tmp_path):
    led = PaperLedger(tmp_path / "ledger")
    with pytest.raises(LedgerError) as exc:
        led.soc_at("NOPE", I0)
    assert exc.value.code == "unknown_asset"


def test_a_torn_ledger_file_fails_closed(ledger):
    p = ledger.path("A1")
    p.write_bytes(p.read_bytes() + b'{"type":"setpoint","seq":0')
    fresh = PaperLedger(ledger.dir)
    with pytest.raises(LedgerError) as exc:
        fresh.soc_at("A1", I0)
    assert exc.value.code == "corrupt_ledger"


def test_settlement_goes_through_units(ledger):
    """1 MW for one 15-minute interval at $100/MWh earns $25, not $100 (the PRD's MW-vs-MWh regression)."""
    ledger.record(SPEC, sp(0, +1.0))
    s = ledger.settle(SPEC, prices(1))
    assert s["energy_usd"].iloc[0] == pytest.approx(25.0) == pytest.approx(units.interval_energy_revenue_usd(100.0, 1.0))
    assert s["wear_usd"].iloc[0] == pytest.approx(20.0 * 0.25)             # $20/MWh on 0.25 MWh
    assert s["revenue_usd"].iloc[0] == pytest.approx(25.0 - 5.0)


def test_settlement_worked_example(ledger):
    ledger.record(SPEC, sp(0, -8.0))                                        # buy 2 MWh at $20
    ledger.record(SPEC, sp(1, +6.0, {"REGUP": 2.0, "RRS": 1.0}))            # sell 1.5 MWh at $90, hold 3 MW of up AS
    ledger.record(SPEC, sp(2, 0.0, {"REGDN": 4.0}))                         # idle, 4 MW of REGDN
    px = prices(3, rt=[20.0, 90.0, 55.0], **{as_price_col("REGUP"): [1.0, 4.0, 0.0], as_price_col("RRS"): [1.0, 6.0, 0.0],
                                           as_price_col("REGDN"): [0.0, 0.0, 10.0]})
    s = ledger.settle(SPEC, px)
    assert list(s["energy_usd"]) == pytest.approx([-8 * 0.25 * 20.0, 6 * 0.25 * 90.0, 0.0])
    assert list(s["as_usd"]) == pytest.approx([0.0, (2 * 4.0 + 1 * 6.0) * 0.25, 4 * 10.0 * 0.25])
    assert list(s["wear_usd"]) == pytest.approx([0.0, 20.0 * 6 * 0.25, 0.0])
    total = -40.0 + (135.0 - 30.0 + 3.5) + 10.0
    assert s["revenue_usd"].sum() == pytest.approx(total)
    assert ledger.revenue_usd(SPEC, px) == pytest.approx(total)
    assert ledger.revenue_usd(SPEC, px, start_utc=I0 + STEP, end_utc=I0 + 2 * STEP) == pytest.approx(135.0 - 30.0 + 3.5)


def test_settlement_without_a_price_fails_loudly(ledger):
    ledger.record(SPEC, sp(0, 2.0, {"REGUP": 1.0}))
    with pytest.raises(LedgerError) as exc:
        ledger.settle(SPEC, prices(1))                                      # no AS price for an AS award
    assert exc.value.code == "missing_price"
    with pytest.raises(LedgerError):
        ledger.settle(SPEC, prices(1).iloc[0:0])                            # no RT price at all


def test_replay_reproduces_settlement_and_the_soc_in_the_file(ledger):
    for k, net in enumerate([-8.0, -6.0, 0.0, 5.0, 9.0, -3.0]):
        ledger.record(SPEC, sp(k, net, {"REGUP": 1.0} if k == 3 else {}))
    px = prices(6, rt=[20.0, 25.0, 40.0, 80.0, 120.0, 30.0], **{as_price_col("REGUP"): 2.0})
    rep = ledger.replay(SPEC, px)
    assert rep.ok and rep.n_intervals == 6 and rep.soc_max_abs_diff_mwh < 1e-9
    assert rep.revenue_usd == pytest.approx(ledger.revenue_usd(SPEC, px))
    sub = ledger.replay(SPEC, px, start_utc=I0 + 2 * STEP, end_utc=I0 + 5 * STEP)
    assert sub.n_intervals == 3 and sub.revenue_usd == pytest.approx(ledger.revenue_usd(SPEC, px, start_utc=I0 + 2 * STEP, end_utc=I0 + 5 * STEP))


def test_replay_notices_a_ledger_that_was_edited_by_hand(ledger):
    for k, net in enumerate([-8.0, -6.0, 5.0]):
        ledger.record(SPEC, sp(k, net))
    px = prices(3)
    assert ledger.replay(SPEC, px).ok
    p = ledger.path("A1")
    # a hand edit of a stored SOC
    edited_soc = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    edited_soc[2]["soc_end_mwh"] += 0.5
    p.write_text("\n".join(json.dumps(x, sort_keys=True) for x in edited_soc) + "\n", encoding="utf-8")
    rep = ledger.replay(SPEC, px)
    assert not rep.ok and rep.soc_max_abs_diff_mwh >= 0.5 - 1e-9
    # a hand edit of two power values: the stored SOC no longer adds up
    edited_net = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    edited_net[2]["soc_end_mwh"] -= 0.5
    edited_net[1]["net_mw"] = -10.0
    edited_net[2]["net_mw"] = -10.0
    p.write_text("\n".join(json.dumps(x, sort_keys=True) for x in edited_net) + "\n", encoding="utf-8")
    rep = ledger.replay(SPEC, px)
    assert not rep.ok and any("limit_violation" in x or "SOC" in x for x in rep.problems)


def test_the_ledger_hands_the_attribution_engine_what_the_asset_did(ledger):
    ledger.record(SPEC, sp(0, -8.0))
    ledger.record(SPEC, sp(1, +6.0, {"REGUP": 2.0}))
    a = ledger.as_actual(SPEC)
    assert a.asset_id == "A1" and a.source == "paper-ledger" and list(a.frame.index) == [I0, I0 + STEP]
    assert list(a.frame["net_mw"]) == [-8.0, 6.0] and list(a.frame["as_regup_mw"]) == [0.0, 2.0]
    assert list(a.frame["available_mw"]) == [10.0, 10.0] and a.frame["soc_mwh"].iloc[-1] == pytest.approx(10.2)
    assert set(a.frame.columns) >= {"net_mw", "available_mw", "soc_mwh"} | {f"as_{p.lower()}_mw" for p in SPEC.as_products}
    assert len(ledger.as_actual(SPEC, start_utc=I0 + STEP).frame) == 1


def test_exposure_is_the_cost_basis_of_the_energy_in_the_battery(ledger):
    e1 = ledger.record(SPEC, sp(0, -8.0), ref_price_usd_per_mwh=200.0)       # buys 2 MWh at an assumed $200 -> $400
    assert e1.cost_basis_end_usd == pytest.approx(400.0)
    pre = ledger.preview(SPEC, sp(1, -4.0), ref_price_usd_per_mwh=300.0)      # would add 1 MWh at $300
    assert pre.cost_basis_start_usd == pytest.approx(400.0) and pre.cost_basis_end_usd == pytest.approx(700.0)
    e2 = ledger.record(SPEC, sp(1, -4.0), ref_price_usd_per_mwh=300.0)
    assert e2.cost_basis_end_usd == pre.cost_basis_end_usd and e2.soc_end_mwh == pytest.approx(pre.soc_end_mwh)
    # discharging 1.5 MWh of the 12.55 MWh above the floor releases that share of the cost
    t = reference_transition(SPEC, e2.soc_end_mwh, e2.cost_basis_end_usd, +6.0)
    assert t.cost_basis_end_usd == pytest.approx(700.0 * (1 - 1.5 / (e2.soc_end_mwh - SPEC.soc_min_mwh)))
    # no quote: the most expensive case is assumed
    no_quote = reference_transition(SPEC, 10.0, 0.0, -4.0, None)
    assert no_quote.cost_basis_end_usd == pytest.approx(ERCOT_PRICE_CAP * 1.0)


@pytest.mark.parametrize("spec_kw", [dict(loss_side="charge"), dict(loss_side="split", rte=0.81)])
def test_ledger_accounting_equals_the_optimizers_when_it_is_importable(ledger, spec_kw):
    """The ledger and D1b's LP must value the same actions identically, or Gate 3 would measure an accounting gap."""
    lp = pytest.importorskip("dispatch.optimizer.lp")
    spec = BatterySpec("A1", "HB_NORTH", power_mw=10.0, energy_mwh=20.0, wear_usd_per_mwh=20.0, **{"rte": 0.85, **spec_kw})
    led = PaperLedger(ledger.dir.parent / "ledger2", fsync=False)
    led.init_asset(spec, 10.0)
    rng = np.random.default_rng(5)
    nets = [0.0, -9.0, -3.0, 4.0, 8.0, 0.0, -6.0, 7.0]
    rt = rng.uniform(-20, 300, len(nets))
    regup, regdn = rng.uniform(0, 8, len(nets)), rng.uniform(0, 8, len(nets))
    px = prices(len(nets), rt=rt, **{as_price_col("REGUP"): regup, as_price_col("REGDN"): regdn})
    for k, net in enumerate(nets):
        led.record(spec, sp(k, net, {"REGUP": 1.0 if net >= 0 else 0.0, "REGDN": 1.5 if net <= 0 else 0.0}, spec=spec))
    mine = led.settle(spec, px)
    entries = led.entries("A1")
    acc = lp.account(spec, px["rt_usd_per_mwh"].to_numpy(), mine["charge_mw"].to_numpy(), mine["discharge_mw"].to_numpy(),
                     {"REGUP": np.array([e.as_mw["REGUP"] for e in entries]), "REGDN": np.array([e.as_mw["REGDN"] for e in entries])},
                     {"REGUP": regup, "REGDN": regdn})
    assert mine["energy_usd"].to_numpy() == pytest.approx(acc["energy_usd"].to_numpy())
    assert mine["as_usd"].to_numpy() == pytest.approx(acc["as_usd"].to_numpy())
    assert mine["wear_usd"].to_numpy() == pytest.approx(acc["wear_usd"].to_numpy())


# ================================================================================================ QSE
def test_the_qse_adapter_is_disabled_and_refuses_every_call():
    assert qse.ENABLED is False
    adapter = qse.get_adapter()
    assert isinstance(adapter, qse.DisabledQSEAdapter) and adapter.enabled is False
    point = sp(0, 1.0)
    for call in (lambda: adapter.submit(point), lambda: adapter.cancel("ref"), lambda: adapter.status("ref")):
        with pytest.raises(qse.QSEDisabledError):
            call()


def test_there_is_no_way_to_get_a_live_adapter_in_v1(monkeypatch):
    monkeypatch.setattr(qse, "ENABLED", True)
    with pytest.raises(RuntimeError):
        qse.get_adapter()


def test_nothing_but_the_paper_ledger_executes():
    """FR9: the service package does not import the QSE module at all, so no cycle can reach it."""
    import pkgutil
    import dispatch.service as svc
    for mod in pkgutil.iter_modules(svc.__path__):
        text = (__import__("pathlib").Path(svc.__path__[0]) / f"{mod.name}.py").read_text(encoding="utf-8")
        assert "execution.qse" not in text and "from .qse" not in text, mod.name
