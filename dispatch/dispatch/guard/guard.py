"""The deterministic risk guard (PRD FR8). It runs on every cycle, outside the model, and it only ever rejects.

    check(spec, setpoint, state, *, now_utc) -> GuardDecision

A pure function: no I/O, no clock, no randomness, no global state. The caller supplies the time (`now_utc`), so a live
cycle, a replay and a property test run the same code on the same inputs and reach the same verdict.

The guard rejects; it never rewrites a setpoint. A limit that is exceeded by no more than the numeric tolerance
`config.GUARD_EPS_MW` (and `GUARD_EPS_MWH`, the same tolerance held for one interval) counts as met, because an LP
solution carries solver noise of that size. An accepted decision carries the caller's setpoint object unchanged.
Anything else (a wrong sign, a NaN, a limit broken by more than the tolerance, a guard bug) is a rejection with one or
more reason codes. DESIGN.md allows clipping inside the tolerance; this module treats "inside the tolerance" as "met"
and does not modify values, so what the audit log shows as proposed is exactly what a ledger executes.

Checks (each gives the reason code in brackets; all that apply are returned, in this order)
  1. the halt flag is set                                                          [halted]
  2. `model_version` is empty                                                      [model_version_missing]
  3. the setpoint is for another asset                                             [asset_mismatch]
  4. a number is NaN or infinite, or a field has the wrong type                    [non_finite, malformed]
     (the check list stops here: later arithmetic on NaN would pass silently)
  5. the interval is not the current or the next 15-minute interval of `now_utc`   [interval_invalid, interval_out_of_window]
  6. SOC telemetry older than MAX_SOC_AGE_S                                        [soc_stale]
  7. SOC reading outside the physical range [0, energy_mwh]                        [soc_telemetry_invalid]
  8. |net_mw| > power_mw                                                           [power_limit]
  9. an AS award that is negative or for a product the asset does not offer        [as_negative, as_product_unknown]
 10. discharge + sum(up awards) > power_mw, or charge + REGDN > power_mw           [as_up_power, as_dn_power]
     (only when an award of that kind is present; discharge alone over the limit is power_limit)
 11. projected end-of-interval SOC above soc_max or below soc_min                  [soc_above_max, soc_below_min]
 12. up awards need energy above soc_min, REGDN needs headroom below soc_max       [as_up_energy, as_dn_headroom]
 13. open exposure above the cap                                                   [exposure_cap]
 14. any exception inside the guard                                                [guard_error]

Formulas (the same ones D1b's LP imposes, so an LP plan is always inside them; see dispatch/optimizer/lp.py):
    charge = max(-net, 0)       discharge = max(net, 0)                     (net_mw > 0 is discharge)
    soc_end = soc0 + eta_c * charge * dt - discharge * dt / eta_d           eta_c, eta_d from BatterySpec
    up awards  = REGUP, RRS, ECRS, NSPIN        sum_up dur_k * r_k / eta_d <= soc_end - soc_min
    down award = REGDN                          eta_c * dur_REGDN * r_REGDN <= soc_max - soc_end
    dur_k = config.AS_DURATION_H[k] (NPRR1282). AS energy is checked on the projected end-of-interval SOC, the way an
    interval-end LP states it. Checking the start-of-interval SOC too would reject plans that ramp an award up while
    the battery charges.

What it reuses from resilience/ (read-only): a setpoint for an interval that has already ended is refused the way
`EpochDevice` refuses an expired or stale-epoch command (resilience/runtime/device.py); the halt flag works like a lease
that nobody holds (no lease, no command); `project_soc_end` is the same kind of model-free conservation check that
resilience/physics/balance.py applies to feeder power.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .. import units
from ..config import AS_DURATION_H, DT_RT_H, GUARD_EPS_MW
from ..contracts import BatterySpec, GuardDecision, GuardState, Setpoint

# ---- named constants
MAX_SOC_AGE_S = 300.0                    # ASSUMPTION: SOC telemetry older than one SCED interval (5 min) is stale; paper SOC has age 0
GUARD_EPS_MWH = GUARD_EPS_MW * DT_RT_H   # DERIVED: the MW tolerance held for one interval, as energy
INTERVAL = pd.Timedelta(minutes=15)      # REAL: ERCOT settlement interval (= DT_RT_H hours)
INTERVAL_FREQ = "15min"
UP_PRODUCTS = tuple(p for p in AS_DURATION_H if p != "REGDN")    # REAL: products that need stored energy to deliver
DOWN_PRODUCTS = ("REGDN",)                                       # REAL: needs headroom to absorb

# ---- reason codes
HALTED = "halted"
MODEL_VERSION_MISSING = "model_version_missing"
ASSET_MISMATCH = "asset_mismatch"
NON_FINITE = "non_finite"
MALFORMED = "malformed"
INTERVAL_INVALID = "interval_invalid"
INTERVAL_OUT_OF_WINDOW = "interval_out_of_window"
SOC_STALE = "soc_stale"
SOC_TELEMETRY_INVALID = "soc_telemetry_invalid"
POWER_LIMIT = "power_limit"
AS_NEGATIVE = "as_negative"
AS_PRODUCT_UNKNOWN = "as_product_unknown"
AS_UP_POWER = "as_up_power"
AS_DN_POWER = "as_dn_power"
SOC_ABOVE_MAX = "soc_above_max"
SOC_BELOW_MIN = "soc_below_min"
AS_UP_ENERGY = "as_up_energy"
AS_DN_HEADROOM = "as_dn_headroom"
EXPOSURE_CAP = "exposure_cap"
GUARD_ERROR = "guard_error"

REASON_CODES = (HALTED, MODEL_VERSION_MISSING, ASSET_MISMATCH, NON_FINITE, MALFORMED, INTERVAL_INVALID,
                INTERVAL_OUT_OF_WINDOW, SOC_STALE, SOC_TELEMETRY_INVALID, POWER_LIMIT, AS_NEGATIVE,
                AS_PRODUCT_UNKNOWN, AS_UP_POWER, AS_DN_POWER, SOC_ABOVE_MAX, SOC_BELOW_MIN, AS_UP_ENERGY,
                AS_DN_HEADROOM, EXPOSURE_CAP, GUARD_ERROR)


@dataclass(frozen=True)
class GuardLimits:
    """Numeric limits the guard itself owns. A smaller value is always a tighter limit (the monotonicity test relies on it)."""
    max_soc_age_s: float = MAX_SOC_AGE_S
    eps_mw: float = GUARD_EPS_MW

    @property
    def eps_mwh(self) -> float:
        return self.eps_mw * DT_RT_H


DEFAULT_LIMITS = GuardLimits()


# ---- shared physics (the ledger, the cycle and the tests call these; one definition of the SOC step)
def project_soc_end(spec: BatterySpec, soc0_mwh: float, net_mw: float, dt_h: float = DT_RT_H) -> float:
    """State of charge at the end of an interval that holds `net_mw` (+ discharge) for `dt_h` hours from `soc0_mwh`."""
    charge_mwh = units.energy_mwh(max(-net_mw, 0.0), dt_h)
    discharge_mwh = units.energy_mwh(max(net_mw, 0.0), dt_h)
    return soc0_mwh + spec.eta_charge * charge_mwh - discharge_mwh / spec.eta_discharge


def current_interval(now_utc: pd.Timestamp) -> pd.Timestamp:
    """Start of the 15-minute interval that contains `now_utc` (tz-aware)."""
    return now_utc.tz_convert("UTC").floor(INTERVAL_FREQ)


# ---- helpers
def _finite(x) -> bool:
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def _is_ts(x) -> bool:
    return isinstance(x, pd.Timestamp) and x is not pd.NaT and x.tzinfo is not None


def _checked_utc(now_utc) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(now_utc)
        return ts.tz_convert("UTC") if ts.tzinfo is not None else pd.NaT
    except Exception:
        return pd.NaT


def check(spec: BatterySpec, setpoint: Setpoint, state: GuardState, *, now_utc: pd.Timestamp,
          limits: GuardLimits = DEFAULT_LIMITS) -> GuardDecision:
    """Accept or reject one setpoint. Never raises: an exception inside the guard is a rejection ("guard_error")."""
    try:
        reasons = _evaluate(spec, setpoint, state, now_utc, limits)
    except Exception:                                   # fail closed: a guard error means no dispatch
        reasons = [GUARD_ERROR]
    checked = _checked_utc(now_utc)
    if reasons:
        return GuardDecision(False, None, tuple(reasons), checked)
    return GuardDecision(True, setpoint, (), checked)


def _evaluate(spec, sp, state, now_utc, limits) -> list:
    reasons: list = []
    eps, eps_h = limits.eps_mw, limits.eps_mwh

    # 1-3: flags that need no arithmetic
    if state.halted is not False:                       # anything but a plain False halts (a string "false" included)
        reasons.append(HALTED)
    if not isinstance(sp.model_version, str) or not sp.model_version.strip():
        reasons.append(MODEL_VERSION_MISSING)
    if sp.asset_id != spec.asset_id:
        reasons.append(ASSET_MISMATCH)

    # 4: types and finiteness. Every later comparison assumes finite numbers, so stop here on a failure.
    as_mw = sp.as_mw
    if not isinstance(as_mw, Mapping):
        return reasons + [MALFORMED]
    spec_vals = (spec.power_mw, spec.energy_mwh, spec.soc_min_mwh, spec.soc_hi_mwh, spec.rte)
    state_vals = (state.soc_mwh, state.soc_age_s, state.exposure_usd)
    cap = state.exposure_cap_usd
    if not (all(_finite(v) for v in spec_vals) and _finite(sp.net_mw) and all(_finite(v) for v in as_mw.values())
            and all(_finite(v) for v in state_vals) and _finite_or_pos_inf(cap)
            and _finite(limits.max_soc_age_s) and _finite(limits.eps_mw)):
        return reasons + [NON_FINITE]
    if spec.power_mw <= 0 or spec.energy_mwh <= 0 or not 0 < spec.rte <= 1 or limits.eps_mw < 0:
        return reasons + [MALFORMED]

    # 5: the interval must be the current or the next one (an interval that has ended is never executed)
    if not _is_ts(sp.interval_start_utc) or not _is_ts(_to_ts(now_utc)):
        reasons.append(INTERVAL_INVALID)
    else:
        cur = current_interval(_to_ts(now_utc))
        start = sp.interval_start_utc.tz_convert("UTC")
        if start != cur and start != cur + INTERVAL:
            reasons.append(INTERVAL_OUT_OF_WINDOW)

    # 6-7: SOC telemetry
    if state.soc_age_s > limits.max_soc_age_s:
        reasons.append(SOC_STALE)
    if state.soc_mwh < -eps_h or state.soc_mwh > spec.energy_mwh + eps_h:
        reasons.append(SOC_TELEMETRY_INVALID)

    # 8: power
    net = float(sp.net_mw)
    charge, discharge = max(-net, 0.0), max(net, 0.0)
    if abs(net) > spec.power_mw + eps:
        reasons.append(POWER_LIMIT)

    # 9-10: ancillary awards
    up_mw = dn_mw = 0.0
    need_up_mwh = 0.0
    for product, award in as_mw.items():
        if product not in spec.as_products or product not in AS_DURATION_H:
            reasons.append(AS_PRODUCT_UNKNOWN)
            continue
        award = float(award)
        if award < -eps:
            reasons.append(AS_NEGATIVE)
        award = max(award, 0.0)
        if product in DOWN_PRODUCTS:
            dn_mw += award
        else:
            up_mw += award
            need_up_mwh += units.energy_mwh(award, AS_DURATION_H[product])      # MW x h = MWh held back for delivery
    reasons = _dedupe(reasons)
    if up_mw > 0 and discharge + up_mw > spec.power_mw + eps:         # (discharge alone over the limit is power_limit)
        reasons.append(AS_UP_POWER)
    if dn_mw > 0 and charge + dn_mw > spec.power_mw + eps:
        reasons.append(AS_DN_POWER)

    # 11-12: end-of-interval SOC and the energy behind the awards
    soc_end = project_soc_end(spec, float(state.soc_mwh), net)
    if not math.isfinite(soc_end):
        return reasons + [NON_FINITE]
    if soc_end > spec.soc_hi_mwh + eps_h:
        reasons.append(SOC_ABOVE_MAX)
    if soc_end < spec.soc_min_mwh - eps_h:
        reasons.append(SOC_BELOW_MIN)
    need_up_stored = need_up_mwh / spec.eta_discharge
    need_dn_stored = spec.eta_charge * units.energy_mwh(dn_mw, AS_DURATION_H["REGDN"])
    if not (math.isfinite(need_up_stored) and math.isfinite(need_dn_stored)):
        return reasons + [NON_FINITE]
    if need_up_stored > (soc_end - spec.soc_min_mwh) + eps_h:
        reasons.append(AS_UP_ENERGY)
    if need_dn_stored > (spec.soc_hi_mwh - soc_end) + eps_h:
        reasons.append(AS_DN_HEADROOM)

    # 13: exposure
    if state.exposure_usd > cap:
        reasons.append(EXPOSURE_CAP)
    return _dedupe(reasons)


def _finite_or_pos_inf(x) -> bool:
    """The exposure cap may be +inf (no cap). NaN and -inf are not valid caps."""
    try:
        return math.isfinite(x) or x == math.inf
    except (TypeError, ValueError):
        return False


def _to_ts(x):
    try:
        return pd.Timestamp(x)
    except Exception:
        return None


def _dedupe(reasons: list) -> list:
    seen, out = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out
