"""Shared data contracts and interfaces (DESIGN.md, Contracts). Owners implement the Protocols; everyone codes
against these types so the offline backtest, the shadow run and the live cycle share one vocabulary.

DataFrames are pandas, indexed by tz-aware UTC `interval_start_utc` on the 15-minute grid unless stated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, Sequence

import pandas as pd

from .config import AS_DURATION_H, AS_PRODUCTS, DEFAULT_RTE, DEFAULT_WEAR_USD_PER_MWH

# ----------------------------------------------------------------------------------------------- column names
RT = "rt_usd_per_mwh"
DAM = "dam_usd_per_mwh"


def as_price_col(product: str) -> str:
    return f"as_{product.lower()}_usd_per_mw_h"


def as_award_col(product: str) -> str:
    return f"as_{product.lower()}_mw"


QUANTILES = ("p10", "p50", "p90")
PLAN_COLUMNS = ("charge_mw", "discharge_mw", "soc_end_mwh", "energy_usd", "as_usd", "wear_usd")
ATTRIBUTION_COLUMNS = ("actual_usd", "shadow_usd", "pf_usd", "gap_usd", "forecast_usd", "soc_usd", "as_usd",
                       "availability_usd", "unexplained_usd")


# ----------------------------------------------------------------------------------------------- the battery
@dataclass(frozen=True)
class BatterySpec:
    asset_id: str
    settlement_point: str                    # ERCOT resource node, hub or load zone the asset settles at
    power_mw: float                          # inverter limit, both directions
    energy_mwh: float                        # usable energy at 100% state of charge
    soc_min_mwh: float = 0.0
    soc_max_mwh: Optional[float] = None      # None -> energy_mwh
    rte: float = DEFAULT_RTE                 # round-trip efficiency
    loss_side: str = "charge"                # "charge": all losses on charging (research arena); "split": sqrt each way
    wear_usd_per_mwh: float = DEFAULT_WEAR_USD_PER_MWH   # charged per MWh discharged
    max_cycles_per_day: Optional[float] = None
    as_products: Sequence[str] = AS_PRODUCTS

    def __post_init__(self):
        if self.power_mw <= 0 or self.energy_mwh <= 0:
            raise ValueError("power_mw and energy_mwh must be positive")
        if not 0 < self.rte <= 1:
            raise ValueError("rte must be in (0, 1]")
        if self.loss_side not in ("charge", "split"):
            raise ValueError("loss_side must be 'charge' or 'split'")
        hi = self.soc_hi_mwh
        if not 0 <= self.soc_min_mwh < hi <= self.energy_mwh + 1e-9:
            raise ValueError("need 0 <= soc_min_mwh < soc_max_mwh <= energy_mwh")
        unknown = set(self.as_products) - set(AS_DURATION_H)
        if unknown:
            raise ValueError(f"unknown AS products {sorted(unknown)}")

    @property
    def soc_hi_mwh(self) -> float:
        return self.energy_mwh if self.soc_max_mwh is None else self.soc_max_mwh

    @property
    def duration_h(self) -> float:
        return self.energy_mwh / self.power_mw

    @property
    def eta_charge(self) -> float:
        return self.rte if self.loss_side == "charge" else self.rte ** 0.5

    @property
    def eta_discharge(self) -> float:
        return 1.0 if self.loss_side == "charge" else self.rte ** 0.5


# ----------------------------------------------------------------------------------------------- forecasts
@dataclass
class ForecastFrame:
    issued_utc: pd.Timestamp
    settlement_point: str
    model_version: str
    frame: pd.DataFrame                      # columns "{series}_{q}" e.g. rt_p10, rt_p50, rt_p90, as_regup_p50
    max_feature_published_utc: pd.Timestamp

    def price_view(self, q: str = "p50") -> pd.DataFrame:
        """A PriceFrame-shaped view at one quantile: rt_p50 -> rt_usd_per_mwh, as_regup_p50 -> as_regup_usd_per_mw_h."""
        out = {}
        for c in self.frame.columns:
            if not c.endswith("_" + q):
                continue
            series = c[: -len(q) - 1]
            if series == "rt":
                out[RT] = self.frame[c]
            elif series == "dam":
                out[DAM] = self.frame[c]
            elif series.startswith("as_"):
                out[f"{series}_usd_per_mw_h"] = self.frame[c]
        return pd.DataFrame(out, index=self.frame.index)


# ----------------------------------------------------------------------------------------------- plans and setpoints
@dataclass
class Plan:
    asset_id: str
    mode: str                                # "pf" (realised prices), "forecast" or "fixed" (replay of given actions)
    solver: str
    status: str                              # "optimal" or the solver's status; anything else means no dispatch
    objective_usd: float
    inputs_sha256: str
    frame: pd.DataFrame                      # PLAN_COLUMNS + as_<product>_mw, on the 15-minute index

    @property
    def ok(self) -> bool:
        return self.status == "optimal"


@dataclass(frozen=True)
class Setpoint:
    asset_id: str
    interval_start_utc: pd.Timestamp
    net_mw: float                            # + discharge (injection), - charge
    as_mw: Mapping[str, float]               # product -> MW offered/awarded for this interval
    plan_id: str
    model_version: str
    issued_utc: pd.Timestamp


@dataclass(frozen=True)
class GuardState:
    soc_mwh: float                           # latest telemetered (or paper) state of charge
    soc_age_s: float
    halted: bool
    halt_reason: str = ""
    exposure_usd: float = 0.0                # open exposure the exposure cap checks against
    exposure_cap_usd: float = float("inf")


@dataclass(frozen=True)
class GuardDecision:
    accepted: bool
    setpoint: Optional[Setpoint]             # the setpoint, unchanged, when accepted; None when rejected
    reasons: tuple                           # machine-readable reason codes, e.g. ("soc_above_max",)
    checked_utc: pd.Timestamp


@dataclass
class CycleResult:
    asset_id: str
    started_utc: pd.Timestamp
    finished_utc: pd.Timestamp
    status: str                              # dispatched | rejected | halted | stale | error
    setpoint: Optional[Setpoint] = None
    decision: Optional[GuardDecision] = None
    reasons: tuple = ()
    model_version: str = ""
    feature_age_s: Optional[float] = None
    audit_hash: str = ""


# ----------------------------------------------------------------------------------------------- interfaces
class Forecaster(Protocol):
    version: str

    def predict(self, features: pd.DataFrame, issued_utc: pd.Timestamp) -> ForecastFrame: ...


class Optimizer(Protocol):
    """`availability` is a FRACTION of power_mw (0..1) per interval. `Plan.frame.wear_usd` is a positive cost and
    `Plan.frame.attrs["diagnostics"]` exists. The real `optimizer.lp.solve` also accepts keyword-only extras, all
    defaulting to None: fixed_energy, charge_availability, charge_limit_mw, soc_min_series, soc_max_series."""

    def __call__(self, spec: BatterySpec, prices: pd.DataFrame, soc0_mwh: float, *, mode: str,
                 allow_as: bool = True, availability: Optional[pd.Series] = None,
                 fixed_as: Optional[pd.DataFrame] = None, terminal_soc_mwh: Optional[float] = None) -> Plan: ...


class OnlineStore(Protocol):
    def write_latest(self, sp: str, df: pd.DataFrame) -> None: ...

    def read_latest(self, sp: str) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]: ...


class HaltFlag(Protocol):
    def is_set(self) -> tuple[bool, str]: ...

    def set(self, reason: str, set_by: str) -> None: ...

    def clear(self, reason: str, cleared_by: str) -> None: ...


@dataclass
class ActualDispatch:
    """What the asset really did (owner export or ERCOT 60-day disclosure), on the 15-minute index.

    frame columns: net_mw (+ discharge), as_<product>_mw (awards), available_mw (max power the asset could deliver,
    derates only, not SOC-limited HSL; NaN if unknown), soc_mwh (telemetered end-of-interval SOC; NaN if unknown).
    Optional columns: available_charge_mw, soc_min_mwh, soc_max_mwh. Absent-row rule: when `source` starts with
    "paper", intervals with no ledger row mean the asset was idle; otherwise they are data gaps (unexplained).
    """
    asset_id: str
    source: str                              # e.g. "ercot-60day-sced", "owner-export:<customer>"
    frame: pd.DataFrame
    notes: Sequence[str] = field(default_factory=tuple)
