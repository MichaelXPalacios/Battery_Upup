"""Battery specs for the grid study, registered at run time (no arena edit).

The arena keeps its battery specs in `arena.config.SPECS` (a plain dict that `arena.battery.Spec(key)` reads). This
module adds a 4-hour grid battery next to the arena's 2-hour `grid` spec by inserting one more entry into that dict
when `gridsite.specs` is imported. Nothing on disk changes, so the arena's own results are untouched.

Every value is a named constant with its label (PLAN.md labels: REAL, DERIVED, SIM, ASSUMPTION):

  grid     1 MW / 2 MWh, arena's own spec, left as it is
  grid4h   1 MW / 4 MWh, added here

Worker processes (spawn on Windows) start with a fresh arena.config, so they must import this module (or call
`register()`) before building `Spec("grid4h")`. `gridsite.runner` does that in its worker functions.
"""
from . import paths

paths.use_arena()
from arena import config as C  # noqa: E402

GRID4H_KEY = "grid4h"

# Product definition: 1 MW of inverter power and 4 MWh of usable energy per MW.
GRID4H_POWER_MW = 1.0              # DERIVED: spec values are per MW of inverter power (as in arena SPECS)
GRID4H_ENERGY_MWH = 4.0            # ASSUMPTION: the 4-hour product the prompt asks for (PROMPT.md section 3, Phase 3)
# The arena tracks energy in whole units of C.U MWh (U = 0.125, a full-power 15-minute interval moves 2 units).
GRID4H_ENERGY_UNITS = int(round(GRID4H_ENERGY_MWH / C.U))   # DERIVED: 4 MWh / 0.125 MWh per unit = 32
GRID4H_RESERVE_UNITS = 0           # ASSUMPTION: no member backup reserve for a merchant battery (as the arena "grid" spec)
GRID4H_RTE = 0.85                  # ASSUMPTION: round-trip efficiency, the same 85% as the arena "grid" spec
GRID4H_CYCLES_PER_DAY = 4.0        # ASSUMPTION: physical sanity limit only (as the arena specs); wear is what prices cycling
GRID4H_DEG_USD_MWH = 20.0          # ASSUMPTION: wear cost, $ per MWh discharged (as the arena specs)
GRID4H_LABEL = "Grid-scale merchant (1 MW / 4 MWh)"

# One row per value: (value, label, source / note). Used by notes/phase3a.md and by anyone who wants the provenance.
GRID4H_PROVENANCE = {
    "energy_units": (GRID4H_ENERGY_UNITS, "DERIVED", "4 MWh / U, with U = arena.config.U = 0.125 MWh per state unit"),
    "reserve_units": (GRID4H_RESERVE_UNITS, "ASSUMPTION", "merchant grid battery: no backup reserve, same as arena 'grid'"),
    "rte": (GRID4H_RTE, "ASSUMPTION", "same 85% round trip as arena 'grid' (loss booked on the charge side)"),
    "cycles_per_day": (GRID4H_CYCLES_PER_DAY, "ASSUMPTION",
                       "physical limit, 4 usable cycles a day, same as arena specs (daily discharge cap = 128 units = 16 MWh)"),
    "deg_usd_mwh": (GRID4H_DEG_USD_MWH, "ASSUMPTION", "wear $20 per MWh discharged, same as arena (replaces a hard cycle cap)"),
    "label": (GRID4H_LABEL, "DERIVED", "text"),
}

GRID4H_SPEC = {
    "label": GRID4H_LABEL,
    "energy_units": GRID4H_ENERGY_UNITS,
    "reserve_units": GRID4H_RESERVE_UNITS,
    "rte": GRID4H_RTE,
    "cycles_per_day": GRID4H_CYCLES_PER_DAY,
    "deg_usd_mwh": GRID4H_DEG_USD_MWH,
}

# Spec keys the grid study runs. The two-hour spec is the arena's own entry.
GRID_SPEC_KEYS = ("grid", GRID4H_KEY)


def register():
    """Add `grid4h` to arena.config.SPECS. Idempotent; refuses to overwrite a different entry of the same name."""
    cur = C.SPECS.get(GRID4H_KEY)
    if cur is None:
        C.SPECS[GRID4H_KEY] = dict(GRID4H_SPEC)
    elif cur != GRID4H_SPEC:
        raise ValueError(f"arena.config.SPECS[{GRID4H_KEY!r}] already exists with different values: {cur}")
    return C.SPECS[GRID4H_KEY]


def spec(key):
    """`arena.battery.Spec(key)` after making sure the grid specs are registered."""
    register()
    from arena.battery import Spec
    return Spec(key)


def hours(key):
    """Duration in hours at full power: usable energy per MW (energy units minus reserve, times U)."""
    register()
    d = C.SPECS[key]
    return (d["energy_units"] - d["reserve_units"]) * C.U


register()
