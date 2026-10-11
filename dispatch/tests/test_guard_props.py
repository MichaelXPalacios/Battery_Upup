"""Property tests for the risk guard (PRD FR8; success metric "Guard integrity: zero").

The guard is a pure function, so Hypothesis can hammer it. The oracle below recomputes every invariant from first
principles (plain floats, literal NPRR1282 durations, no dispatch.units, no dispatch.guard helpers), so a mistake in the
guard cannot hide behind the same mistake in the test.

  soundness       every accepted setpoint satisfies every invariant (the "zero guard-violating setpoints" metric)
  completeness    a setpoint that is comfortably inside every limit is accepted (the guard does not over-reject)
  halted          a set halt flag rejects everything
  fuzz            garbage in (NaN, inf, wrong types, naive times) never raises, never accepts
  monotonicity    tightening any limit never turns a rejection into an acceptance
  examples        one hand-built case per reason code, and the boundary of each limit
"""
import dataclasses
import math
import os
from collections import Counter

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from dispatch.config import AS_PRODUCTS, GUARD_EPS_MW
from dispatch.contracts import BatterySpec, GuardDecision, GuardState, Setpoint
from dispatch.guard import guard as G
from dispatch.guard.guard import GuardLimits, check, project_soc_end

N = int(os.environ.get("GUARD_PROP_EXAMPLES", "1500"))
SETTINGS = dict(max_examples=N, deadline=None, database=None,
                suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])

NOW = pd.Timestamp("2026-10-10 15:07:30", tz="UTC")
CUR = pd.Timestamp("2026-10-10 15:00:00", tz="UTC")
STEP = pd.Timedelta(minutes=15)

# NPRR1282 durations, written out again on purpose: a typo in dispatch.config would fail the oracle.
DUR = {"REGUP": 0.5, "REGDN": 0.5, "RRS": 0.5, "ECRS": 1.0, "NSPIN": 4.0}
UP = ("REGUP", "RRS", "ECRS", "NSPIN")
DT = 0.25


# ------------------------------------------------------------------------------------------------ the oracle
def etas(spec):
    if spec.loss_side == "charge":
        return spec.rte, 1.0
    return math.sqrt(spec.rte), math.sqrt(spec.rte)


def oracle(spec, sp, state, now, *, max_age=G.MAX_SOC_AGE_S, eps=GUARD_EPS_MW, pad=0.0):
    """Violated invariants, recomputed from scratch. `pad` > 0 loosens each limit (soundness: only report violations that
    exceed tolerance + pad); `pad` < 0 tightens it (completeness: report anything within |pad| of a limit)."""
    out = []
    e_mw = eps + pad
    e_mwh = eps * DT + pad
    if state.halted is not False:
        out.append("halted")
    if not (isinstance(sp.model_version, str) and sp.model_version.strip()):
        out.append("model_version")
    if sp.asset_id != spec.asset_id:
        out.append("asset")
    nums = [sp.net_mw, state.soc_mwh, state.soc_age_s, state.exposure_usd, *sp.as_mw.values()]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in nums) or math.isnan(state.exposure_cap_usd):
        out.append("non_finite")
        return out
    start = sp.interval_start_utc
    if start is pd.NaT or start.tzinfo is None:
        out.append("interval")
    else:
        cur = now.floor("15min")
        if start not in (cur, cur + STEP):
            out.append("interval")
    if state.soc_age_s > max_age + pad:
        out.append("soc_stale")
    if state.soc_mwh < 0 - e_mwh or state.soc_mwh > spec.energy_mwh + e_mwh:
        out.append("soc_telemetry")
    net = sp.net_mw
    c = -net if net < 0 else 0.0
    d = net if net > 0 else 0.0
    if abs(net) > spec.power_mw + e_mw:
        out.append("power")
    ec, ed = etas(spec)
    up_mw = sum(v for k, v in sp.as_mw.items() if k in UP)
    dn_mw = sp.as_mw.get("REGDN", 0.0)
    for k, v in sp.as_mw.items():
        if k not in spec.as_products:
            out.append("as_unknown")
        if v < 0 - e_mw:
            out.append("as_negative")
    if d + max(up_mw, 0.0) > spec.power_mw + e_mw:
        out.append("as_up_power")
    if c + max(dn_mw, 0.0) > spec.power_mw + e_mw:
        out.append("as_dn_power")
    soc_end = state.soc_mwh + ec * c * DT - d * DT / ed
    if soc_end > spec.soc_hi_mwh + e_mwh:
        out.append("soc_above_max")
    if soc_end < spec.soc_min_mwh - e_mwh:
        out.append("soc_below_min")
    need_up = sum(DUR[k] * max(v, 0.0) for k, v in sp.as_mw.items() if k in UP) / ed
    if need_up > soc_end - spec.soc_min_mwh + e_mwh:
        out.append("as_up_energy")
    need_dn = ec * DUR["REGDN"] * max(dn_mw, 0.0)
    if need_dn > spec.soc_hi_mwh - soc_end + e_mwh:
        out.append("as_dn_headroom")
    if state.exposure_usd > state.exposure_cap_usd:
        out.append("exposure")
    return out


# ------------------------------------------------------------------------------------------------ strategies
# Hypothesis draws cost ~0.3 ms each on this machine, so a case is built from ONE list of unit floats (floats(0, 1) still
# shrinks to 0 and still favours the end points) and mapped to a spec, a state and a setpoint by plain code.
K = 56
UNIT = st.lists(st.floats(0.0, 1.0), min_size=K, max_size=K)


class _U:
    def __init__(self, u):
        self.u = list(u)
        self.i = 0

    def __call__(self, lo=0.0, hi=1.0):
        v = self.u[self.i % len(self.u)]
        self.i += 1
        return lo + (hi - lo) * v


def build_spec(r):
    power = r(0.1, 500.0)
    energy = power * r(0.25, 8.0)
    soc_min = energy * r(0.0, 0.3)
    soc_max = energy * r(0.6, 1.0)
    rte = r(0.5, 1.0)
    split = r() < 0.5
    products = tuple(p for p in sorted(AS_PRODUCTS) if r() < 0.7)
    return BatterySpec(asset_id="A1", settlement_point="HB_NORTH", power_mw=power, energy_mwh=energy,
                       soc_min_mwh=soc_min, soc_max_mwh=soc_max, rte=rte, loss_side="split" if split else "charge",
                       as_products=products)


def build_case(u, halted=None):
    """(spec, setpoint, state, now). About a third are feasible; the rest hit one limit or several, often on the boundary."""
    r = _U(u)
    spec = build_spec(r)
    P, E, lo, hi = spec.power_mw, spec.energy_mwh, spec.soc_min_mwh, spec.soc_hi_mwh
    ec, ed = etas(spec)
    soc0 = lo + (hi - lo) * r() if r() < 0.8 else E * r(-0.1, 1.1)
    max_dis = max(min(P, (soc0 - lo) * ed / DT), 0.0)         # most it can discharge / charge without leaving [lo, hi]
    max_chg = max(min(P, (hi - soc0) / (ec * DT)), 0.0)
    pick, x = r(), r()
    if pick < 0.55:
        net = -max_chg + (max_chg + max_dis) * x
    elif pick < 0.65:
        net = -1.3 * P + 2.6 * P * x
    elif pick < 0.75:
        net = [P, -P, P + GUARD_EPS_MW / 2, P + 2 * GUARD_EPS_MW, -P - 2 * GUARD_EPS_MW][int(x * 4.999)]
    elif pick < 0.88:                                          # charge exactly to the ceiling, then nudge either way
        net = -min(max((hi - soc0) / (ec * DT), 0.0), 2 * P) * [1.0, 1.0, 1.0001, 0.9999][int(x * 3.999)]
    else:
        net = min(max((soc0 - lo) * ed / DT, 0.0), 2 * P) * [1.0, 1.0, 1.0001, 0.9999][int(x * 3.999)]
    as_mw = {}
    for p in AS_PRODUCTS:
        kind, mag, foreign = r(), r(), r()
        if p in spec.as_products or foreign < 0.02:
            as_mw[p] = (0.0 if kind < 0.6 else 0.2 * P * mag if kind < 0.9 else 1.2 * P * mag if kind < 0.985
                        else (-1e-9 if mag < 0.5 else -1.0))
    where, extra = r(), r()
    start = CUR + STEP * (0 if where < 0.45 else 1 if where < 0.93 else -1 if where < 0.96 else 2)
    if extra < 0.03:
        start = start + pd.Timedelta(seconds=1 + int(extra * 1e4) % 898)
    elif extra > 0.985:
        start = start.tz_localize(None)
    sp = Setpoint(asset_id="A1" if r() < 0.97 else "B2", interval_start_utc=start, net_mw=net, as_mw=as_mw,
                  plan_id="p", model_version="v1" if r() < 0.97 else ["", "  "][int(r() * 1.999)], issued_utc=NOW)
    age = r(0.0, 250.0) if r() < 0.92 else r(0.0, 900.0)
    cap = r(0.0, 1e5) if r() < 0.15 else math.inf
    is_halted = r() < 0.04 if halted is None else halted
    r()                                                        # keep the stream aligned whatever `halted` is
    state = GuardState(soc_mwh=soc0, soc_age_s=age, halted=is_halted, exposure_usd=r(0.0, 5e4), exposure_cap_usd=cap)
    return spec, sp, state, NOW


# ------------------------------------------------------------------------------------------------ soundness
@settings(**SETTINGS)
@given(UNIT)
def test_every_accepted_setpoint_satisfies_every_invariant(u):
    """The PRD metric 'Guard integrity': no setpoint that breaks a limit gets through. Checked three ways per case:
    soundness (accepted => no violation beyond tolerance), completeness (clearly inside every limit => accepted) and
    agreement (a violation beyond tolerance + 1e-6 => rejected)."""
    spec, sp, state, now = build_case(u)
    d = check(spec, sp, state, now_utc=now)
    assert isinstance(d, GuardDecision)
    assert d.checked_utc == now
    if d.accepted:
        assert d.setpoint is sp, "an accepted setpoint passes through unchanged"
        assert d.reasons == ()
        assert oracle(spec, sp, state, now, pad=1e-9) == [], oracle(spec, sp, state, now, pad=1e-9)
    else:
        assert d.setpoint is None and len(d.reasons) >= 1
        assert set(d.reasons) <= set(G.REASON_CODES)
        assert oracle(spec, sp, state, now, pad=-1e-6) != [], "rejected although clearly inside every limit"
    if oracle(spec, sp, state, now, pad=1e-6):
        assert not d.accepted


def test_generator_reaches_both_outcomes():
    """A property test that never sees an acceptance proves nothing, so count what the generator produces.

    The count is a statistic of Hypothesis's random draws, so it is pinned with `derandomize=True`: the same 1500 cases on
    every run and every machine (I1, 10 Oct 2026). Before, it drew fresh cases each time and failed once with 198 acceptances
    against the 200 needed: over seeds 0 to 7 the acceptances ranged from 203 to 342 of 1500, so the bar sat inside the noise.
    Pinned, it sees 268 acceptances, 1232 rejections and 16 reason codes. The bars below are unchanged."""
    seen = Counter()
    rejected_for = Counter()

    # Lead, 11 Oct: derandomize=True still drew 193 acceptances once (P2's full run), so the count no longer goes through
    # Hypothesis at all: 1500 cases from a seeded NumPy generator are exactly reproducible. The property tests stay on Hypothesis.
    rng = np.random.default_rng(20261011)
    for u in rng.random((1500, K)):
        spec, sp, state, now = build_case([float(x) for x in u], halted=False)
        d = check(spec, sp, state, now_utc=now)
        seen[d.accepted] += 1
        rejected_for.update(d.reasons)
    assert seen[True] >= 200 and seen[False] >= 200, seen
    # the generator should provoke most reason codes, otherwise the soundness test is weaker than it looks
    assert len(rejected_for) >= 14, rejected_for


# ------------------------------------------------------------------------------------------------ halted
@settings(**{**SETTINGS, "max_examples": max(N // 3, 300)})
@given(UNIT)
def test_halted_means_always_rejected(u):
    spec, sp, state, now = build_case(u, halted=True)
    d = check(spec, sp, state, now_utc=now)
    assert not d.accepted and d.setpoint is None
    assert G.HALTED in d.reasons


@settings(**{**SETTINGS, "max_examples": max(N // 6, 200)})
@given(UNIT, st.sampled_from([True, None, "false", 0, 1, "", "no", float("nan")]))
def test_halted_is_fail_closed_for_anything_but_plain_false(u, flag):
    spec, sp, state, now = build_case(u, halted=False)
    d = check(spec, sp, dataclasses.replace(state, halted=flag), now_utc=now)
    assert not d.accepted and G.HALTED in d.reasons


# ------------------------------------------------------------------------------------------------ fuzz
SPEC_POOL = [build_spec(_U([(i * 0.137 + j * 0.0713) % 1.0 for j in range(K)])) for i in range(24)]
weird = st.one_of(st.floats(allow_nan=True, allow_infinity=True), st.integers(-10**9, 10**9), st.none(),
                  st.text(max_size=4), st.just(5e-324), st.just(1e308), st.just(-1e308), st.booleans(),
                  st.lists(st.floats(), max_size=2))
weird_time = st.one_of(st.just(pd.NaT), st.none(), st.integers(), st.text(max_size=5), st.just(NOW),
                       st.just(NOW.tz_localize(None)), st.just(CUR), st.just(CUR + STEP),
                       st.just(pd.Timestamp("2262-01-01", tz="UTC")), st.just(pd.Timestamp("1970-01-01", tz="UTC")),
                       st.just(pd.Timestamp("2026-10-10 15:00", tz="America/Chicago")))
weird_as = st.one_of(st.none(), st.integers(), st.lists(st.integers(), max_size=2),
                     st.dictionaries(st.one_of(st.sampled_from(AS_PRODUCTS), st.text(max_size=4), st.none(),
                                               st.integers()), weird, max_size=4))


@settings(**{**SETTINGS, "max_examples": max(N // 2, 500)})
@given(spec=st.sampled_from(SPEC_POOL), net=weird, as_mw=weird_as, start=weird_time, now=weird_time, version=weird,
       soc=weird, age=weird, halted=weird, expo=weird, cap=weird)
def test_fuzz_never_raises_and_never_accepts_garbage(spec, net, as_mw, start, now, version, soc, age, halted, expo, cap):
    sp = Setpoint("A1", start, net, as_mw, "p", version, NOW)
    state = GuardState(soc, age, halted, "", expo, cap)
    d = check(spec, sp, state, now_utc=now)                  # must not raise, whatever the types
    assert isinstance(d, GuardDecision)
    if d.accepted:                                            # garbage must never get through
        numbers = [net, soc, age, expo, *as_mw.values()]
        assert all(type(v) in (int, float) and math.isfinite(v) for v in numbers)
        assert halted is False and cap == cap and isinstance(version, str) and version.strip()
        assert isinstance(start, pd.Timestamp) and start.tzinfo is not None and isinstance(now, pd.Timestamp)


@settings(**{**SETTINGS, "max_examples": max(N // 2, 500)})
@given(spec=st.sampled_from(SPEC_POOL), net=st.floats(), soc=st.floats(), age=st.floats(), expo=st.floats(),
       cap=st.floats(), a=st.floats(), b=st.floats())
def test_typed_fuzz_with_nan_and_inf_has_no_internal_errors(spec, net, soc, age, expo, cap, a, b):
    """Well-typed inputs (floats including NaN and +-inf, or 1e308) are rejected for a reason, never by a guard bug."""
    sp = Setpoint("A1", CUR, net, {"REGUP": a, "REGDN": b}, "p", "v1", NOW)
    d = check(spec, sp, GuardState(soc, age, False, "", expo, cap), now_utc=NOW)
    assert G.GUARD_ERROR not in d.reasons
    if not all(map(math.isfinite, (net, soc, age, expo, a, b))) or math.isnan(cap):
        assert not d.accepted


# ------------------------------------------------------------------------------------------------ monotonicity
def tighten(u, v):
    """The same case with every selected limit made tighter (or equal). `v` picks the dimensions and the factors."""
    spec, sp, state, now = build_case(u)
    P, E, lo, hi = spec.power_mw, spec.energy_mwh, spec.soc_min_mwh, spec.soc_hi_mwh

    def pick(i):
        return v[i] < 0.5                                      # each dimension is tightened about half of the time

    kw = {}
    new_lo, new_hi = lo, hi
    if pick(0):
        kw["power_mw"] = P * (0.3 + 0.7 * v[11])
    if pick(1):
        new_lo = lo + (hi - lo) * 0.8 * v[12]
    if pick(2):
        new_hi = new_lo + (hi - new_lo) * (0.2 + 0.8 * v[13])
    new_hi = max(min(new_hi, hi), new_lo + 1e-3 * E)
    if pick(3):
        kw["energy_mwh"] = max(new_hi, E * (0.5 + 0.5 * v[14]))
    kw["soc_min_mwh"], kw["soc_max_mwh"] = new_lo, new_hi
    if pick(4) and spec.as_products:
        kw["as_products"] = tuple(spec.as_products[: int(v[15] * len(spec.as_products) * 0.999)])
    try:
        t_spec = dataclasses.replace(spec, **kw)
    except ValueError:
        t_spec = spec
    sk = {}
    if pick(5):
        sk["exposure_cap_usd"] = (state.exposure_cap_usd * v[16] if math.isfinite(state.exposure_cap_usd) else 1e5 * v[16])
    if pick(6):
        sk["exposure_usd"] = state.exposure_usd + 1e4 * v[17]
    if pick(7):
        sk["soc_age_s"] = state.soc_age_s + 600 * v[18]
    if pick(8):
        sk["halted"] = True
    t_state = dataclasses.replace(state, **sk)
    limits = GuardLimits(max_soc_age_s=G.MAX_SOC_AGE_S * (v[19] if pick(9) else 1.0),
                         eps_mw=GUARD_EPS_MW * (v[20] if pick(10) else 1.0))
    return (spec, sp, state, now), (t_spec, sp, t_state, now, limits)


@settings(**SETTINGS)
@given(UNIT, st.lists(st.floats(0.0, 1.0), min_size=21, max_size=21))
def test_tightening_a_limit_never_turns_a_rejection_into_an_acceptance(u, v):
    (spec, sp, state, now), (t_spec, t_sp, t_state, t_now, t_lim) = tighten(u, v)
    loose = check(spec, sp, state, now_utc=now)
    tight = check(t_spec, t_sp, t_state, now_utc=t_now, limits=t_lim)
    if tight.accepted:
        assert loose.accepted, (loose.reasons, oracle(spec, sp, state, now))


# ------------------------------------------------------------------------------------------------ worked examples
def make(**over):
    spec = BatterySpec("A1", "HB_NORTH", power_mw=10.0, energy_mwh=20.0, rte=0.85, loss_side="charge")
    sp = dict(asset_id="A1", interval_start_utc=CUR + STEP, net_mw=0.0, as_mw={}, plan_id="p", model_version="v1",
              issued_utc=NOW)
    st_ = dict(soc_mwh=10.0, soc_age_s=0.0, halted=False)
    for k in list(over):
        if k in st_ or k in ("halt_reason", "exposure_usd", "exposure_cap_usd"):
            st_[k] = over.pop(k)
        elif k in sp:
            sp[k] = over.pop(k)
    spec = dataclasses.replace(spec, **over) if over else spec
    return spec, Setpoint(**sp), GuardState(**st_)


def run(**over):
    spec, sp, state = make(**over)
    return check(spec, sp, state, now_utc=NOW)


def test_a_feasible_setpoint_is_accepted_unchanged():
    spec, sp, state = make(net_mw=-5.0, as_mw={"REGUP": 2.0, "RRS": 1.0, "REGDN": 3.0})
    d = check(spec, sp, state, now_utc=NOW)
    assert d.accepted and d.setpoint is sp and d.reasons == ()


@pytest.mark.parametrize("over, code", [
    (dict(halted=True), "halted"),
    (dict(model_version=""), "model_version_missing"),
    (dict(asset_id="B2"), "asset_mismatch"),
    (dict(net_mw=float("nan")), "non_finite"),
    (dict(net_mw=float("inf")), "non_finite"),
    (dict(soc_mwh=float("nan")), "non_finite"),
    (dict(as_mw={"REGUP": float("nan")}), "non_finite"),
    (dict(exposure_cap_usd=float("nan")), "non_finite"),
    (dict(as_mw=None), "malformed"),
    (dict(interval_start_utc=CUR - STEP), "interval_out_of_window"),
    (dict(interval_start_utc=CUR + 2 * STEP), "interval_out_of_window"),
    (dict(interval_start_utc=CUR + pd.Timedelta(seconds=1)), "interval_out_of_window"),
    (dict(interval_start_utc=CUR.tz_localize(None)), "interval_invalid"),
    (dict(interval_start_utc=pd.NaT), "interval_invalid"),
    (dict(soc_age_s=301.0), "soc_stale"),
    (dict(soc_mwh=-1.0), "soc_telemetry_invalid"),
    (dict(soc_mwh=21.0), "soc_telemetry_invalid"),
    (dict(net_mw=10.5), "power_limit"),
    (dict(net_mw=-10.5), "power_limit"),
    (dict(as_mw={"REGUP": -1.0}), "as_negative"),
    (dict(as_mw={"FOO": 1.0}), "as_product_unknown"),
    (dict(as_mw={"REGUP": 6.0}, net_mw=5.0), "as_up_power"),
    (dict(as_mw={"REGUP": 4.0, "RRS": 4.0, "ECRS": 4.0}), "as_up_power"),
    (dict(as_mw={"REGDN": 6.0}, net_mw=-5.0), "as_dn_power"),
    (dict(net_mw=-10.0, soc_mwh=19.0), "soc_above_max"),          # 19 + 0.85 * 10 * 0.25 = 21.125
    (dict(net_mw=10.0, soc_mwh=1.0), "soc_below_min"),            # 1 - 10 * 0.25 = -1.5
    (dict(as_mw={"NSPIN": 5.0}, soc_mwh=10.0), "as_up_energy"),   # 5 MW x 4 h = 20 MWh > 10
    (dict(as_mw={"ECRS": 5.0}, soc_mwh=4.0), "as_up_energy"),     # 5 MW x 1 h = 5 MWh > 4
    (dict(as_mw={"REGDN": 9.0}, soc_mwh=19.0), "as_dn_headroom"), # 0.85 * 0.5 h * 9 MW = 3.8 MWh > 1
    (dict(exposure_usd=101.0, exposure_cap_usd=100.0), "exposure_cap"),
])
def test_every_reason_code_is_reachable(over, code):
    d = run(**over)
    assert not d.accepted and d.setpoint is None
    assert code in d.reasons, d.reasons


def test_reason_codes_listed_are_all_exercised_somewhere():
    exercised = {"halted", "model_version_missing", "asset_mismatch", "non_finite", "malformed", "interval_invalid",
                 "interval_out_of_window", "soc_stale", "soc_telemetry_invalid", "power_limit", "as_negative",
                 "as_product_unknown", "as_up_power", "as_dn_power", "soc_above_max", "soc_below_min", "as_up_energy",
                 "as_dn_headroom", "exposure_cap"}
    assert exercised | {"guard_error"} == set(G.REASON_CODES)


def test_current_and_next_interval_pass_and_the_previous_one_fails():
    assert run(interval_start_utc=CUR).accepted
    assert run(interval_start_utc=CUR + STEP).accepted
    assert not run(interval_start_utc=CUR - STEP).accepted
    # on the boundary the clock has moved to the next interval: the one that just ended is out
    spec, sp, state = make(interval_start_utc=CUR)
    assert not check(spec, sp, state, now_utc=CUR + STEP).accepted
    assert check(spec, sp, state, now_utc=CUR + STEP - pd.Timedelta(microseconds=1)).accepted


def test_time_zone_does_not_matter_only_the_instant():
    local = (CUR + STEP).tz_convert("America/Chicago")
    assert run(interval_start_utc=local).accepted


def test_eps_boundary_power():
    eps = GUARD_EPS_MW
    assert run(net_mw=10.0 + eps / 2).accepted                       # numeric noise is tolerated ...
    assert not run(net_mw=10.0 + 2 * eps).accepted                   # ... a real excess is not
    assert run(net_mw=10.0).accepted and run(net_mw=-10.0, soc_mwh=5.0).accepted
    assert not run(net_mw=-10.0 - 2 * eps, soc_mwh=5.0).accepted


def test_eps_boundary_soc():
    # charging 5 MW for 15 min stores 0.85 * 5 * 0.25 = 1.0625 MWh
    exact = 20.0 - 0.85 * 5.0 * 0.25
    assert run(net_mw=-5.0, soc_mwh=exact).accepted                  # lands exactly on the ceiling
    assert run(net_mw=-5.0, soc_mwh=exact + 1e-8).accepted           # numeric noise is tolerated
    assert not run(net_mw=-5.0, soc_mwh=exact + 1e-3).accepted       # a real overshoot is not


def test_stale_soc_limit_is_a_named_constant_and_a_parameter():
    assert G.MAX_SOC_AGE_S == 300.0
    spec, sp, state = make(soc_age_s=299.0)
    assert check(spec, sp, state, now_utc=NOW).accepted
    assert not check(spec, sp, state, now_utc=NOW, limits=GuardLimits(max_soc_age_s=60.0)).accepted


def test_split_losses_use_square_root_each_way():
    spec = BatterySpec("A1", "HB_NORTH", 10.0, 20.0, rte=0.81, loss_side="split")      # eta = 0.9 each way
    assert project_soc_end(spec, 10.0, -8.0) == pytest.approx(10.0 + 0.9 * 8.0 * 0.25)
    assert project_soc_end(spec, 10.0, +9.0) == pytest.approx(10.0 - 9.0 * 0.25 / 0.9)
    spec_c = BatterySpec("A1", "HB_NORTH", 10.0, 20.0, rte=0.81, loss_side="charge")   # all losses on charging
    assert project_soc_end(spec_c, 10.0, -8.0) == pytest.approx(10.0 + 0.81 * 8.0 * 0.25)
    assert project_soc_end(spec_c, 10.0, +9.0) == pytest.approx(10.0 - 9.0 * 0.25)


def test_as_energy_uses_nprr1282_durations_and_discharge_efficiency():
    spec = BatterySpec("A1", "HB_NORTH", 10.0, 20.0, rte=0.81, loss_side="split")
    sp = Setpoint("A1", CUR, 0.0, {"ECRS": 4.0}, "p", "v1", NOW)              # 4 MW x 1 h / 0.9 = 4.444 MWh above the floor
    ok = GuardState(soc_mwh=4.5, soc_age_s=0.0, halted=False)
    bad = GuardState(soc_mwh=4.4, soc_age_s=0.0, halted=False)
    assert check(spec, sp, ok, now_utc=NOW).accepted
    assert G.AS_UP_ENERGY in check(spec, sp, bad, now_utc=NOW).reasons


def test_guard_error_is_a_rejection_not_an_exception():
    class Broken:
        asset_id = "A1"
        power_mw = 10.0
    d = check(Broken(), Setpoint("A1", CUR, 0.0, {}, "p", "v1", NOW), GuardState(1.0, 0.0, False), now_utc=NOW)
    assert not d.accepted and d.reasons == (G.GUARD_ERROR,)


def test_the_guard_is_pure():
    """Same inputs, same verdict, and the inputs are not mutated."""
    spec, sp, state = make(net_mw=-4.0, as_mw={"REGUP": 1.0})
    before = (sp.net_mw, dict(sp.as_mw), state.soc_mwh)
    a = check(spec, sp, state, now_utc=NOW)
    b = check(spec, sp, state, now_utc=NOW)
    assert a == b and before == (sp.net_mw, dict(sp.as_mw), state.soc_mwh)
