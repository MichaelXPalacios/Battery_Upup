"""Which assets the service runs cycles for, loaded from `ops/assets.example.json` (or the file named by DISPATCH_ASSETS_FILE).

v1 is paper only: the PRD's first non-goal is "acting as a QSE or submitting live bids". A file that asks for any other
mode is refused at load, so the service cannot start with a live asset by editing JSON.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .. import paths
from ..contracts import BatterySpec

DEFAULT_ASSETS_FILE = paths.PROJECT_DIR / "ops" / "assets.example.json"
MODES = ("paper",)
_SPEC_KEYS = {"asset_id", "settlement_point", "power_mw", "energy_mwh", "soc_min_mwh", "soc_max_mwh", "rte", "loss_side",
              "wear_usd_per_mwh", "max_cycles_per_day", "as_products"}
_EXTRA_KEYS = {"mode", "initial_soc_mwh", "exposure_cap_usd", "labels"}


class UnknownAsset(KeyError):
    pass


@dataclass(frozen=True)
class AssetConfig:
    spec: BatterySpec
    mode: str = "paper"
    initial_soc_mwh: float = 0.0
    exposure_cap_usd: float = math.inf         # the guard's cap on the cost basis of stored energy; inf = no cap
    labels: Mapping = field(default_factory=dict)


class AssetRegistry:
    def __init__(self, assets: Iterable[AssetConfig]):
        self._assets = {}
        for a in assets:
            if a.spec.asset_id in self._assets:
                raise ValueError(f"duplicate asset_id {a.spec.asset_id}")
            self._assets[a.spec.asset_id] = a

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AssetRegistry":
        path = Path(path or os.environ.get("DISPATCH_ASSETS_FILE") or DEFAULT_ASSETS_FILE)
        doc = json.loads(path.read_text(encoding="utf-8"))
        return cls(parse_asset(a) for a in doc["assets"])

    def get(self, asset_id: str) -> AssetConfig:
        try:
            return self._assets[asset_id]
        except KeyError:
            raise UnknownAsset(asset_id) from None

    def __contains__(self, asset_id) -> bool:
        return asset_id in self._assets

    def ids(self) -> list:
        return sorted(self._assets)

    def __len__(self) -> int:
        return len(self._assets)


def parse_asset(raw: Mapping) -> AssetConfig:
    unknown = set(raw) - _SPEC_KEYS - _EXTRA_KEYS
    if unknown:
        raise ValueError(f"asset {raw.get('asset_id')}: unknown keys {sorted(unknown)}")
    mode = raw.get("mode", "paper")
    if mode not in MODES:
        raise ValueError(f"asset {raw.get('asset_id')}: mode {mode!r} is not supported; v1 is paper only (PRD non-goal: live bids)")
    kw = {k: raw[k] for k in _SPEC_KEYS if k in raw}
    if "as_products" in kw:
        kw["as_products"] = tuple(kw["as_products"])
    spec = BatterySpec(**kw)
    soc0 = float(raw.get("initial_soc_mwh", spec.soc_min_mwh))
    if not (spec.soc_min_mwh <= soc0 <= spec.soc_hi_mwh):
        raise ValueError(f"asset {spec.asset_id}: initial_soc_mwh {soc0} is outside [{spec.soc_min_mwh}, {spec.soc_hi_mwh}]")
    cap = raw.get("exposure_cap_usd")
    cap = math.inf if cap is None else float(cap)
    if cap < 0 or math.isnan(cap):
        raise ValueError(f"asset {spec.asset_id}: exposure_cap_usd must be >= 0 or null")
    return AssetConfig(spec=spec, mode=mode, initial_soc_mwh=soc0, exposure_cap_usd=cap, labels=dict(raw.get("labels", {})))
