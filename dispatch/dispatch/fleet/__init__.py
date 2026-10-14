"""Phase 3, fleet dispatch for ADER aggregators: an interface stub only.

It waits on ADER Phase 4 (ADESR: nodal dispatch, SOC telemetry), which goes to the ERCOT Board in December 2026.
Distribution awareness comes from the hackathon engine: sim/caps.py (transformer_caps: per-transformer charge and
export headroom), sim/surrogate.py (fast loading model) and sim/orchestrator.py (feeder-aware allocate). ERCOT dispatches
a fleet per load zone and enforces no limits at the service transformer; this layer is where those limits get enforced.
"""
from typing import Mapping, Protocol, Sequence

from ..contracts import Setpoint


class HeadroomSource(Protocol):
    def headroom_mw(self, transformer_ids: Sequence[str]) -> Mapping[str, tuple[float, float]]:
        """Per transformer: (max additional charge MW, max additional export MW) right now."""


class FleetAdapter(Protocol):
    def split(self, fleet_setpoint: Setpoint, headroom: HeadroomSource) -> Mapping[str, float]:
        """Split one fleet setpoint across sites without exceeding any transformer's headroom."""


ENABLED = False   # stays False until ADER Phase 4 rules are final
