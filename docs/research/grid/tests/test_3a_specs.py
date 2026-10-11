"""The registered 4-hour spec `grid4h` works with the arena's Spec, oracle_days and run_combined (no arena edit).

Run:  python tests/test_3a_specs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_3a as H  # noqa: E402

import numpy as np  # noqa: E402

from gridsite import paths, specs  # noqa: E402

paths.use_arena()
from arena import config as C  # noqa: E402
from arena.algorithms import History, run_combined  # noqa: E402
from arena.battery import Spec, oracle_days  # noqa: E402


def _synthetic_prices(days, lanes, seed):
    """Daily two-peak shape with noise; $/MWh; shape (days, lanes, 96)."""
    g = np.random.default_rng(seed)
    t = np.arange(96) / 4.0
    base = 35 + 25 * np.exp(-0.5 * ((t - 19) / 2.0) ** 2) + 12 * np.exp(-0.5 * ((t - 8) / 1.5) ** 2) \
        - 20 * np.exp(-0.5 * ((t - 13) / 3.0) ** 2)
    P = base[None, None, :] * g.uniform(0.7, 1.4, size=(days, lanes, 1)) + g.normal(0, 4, size=(days, lanes, 96))
    spike = g.random((days, lanes, 1)) < 0.08
    P = np.where(spike & (t[None, None, :] > 17) & (t[None, None, :] < 20), P * 6, P)
    return P


def _ctx(days, lanes, spec):
    return [{"weekend": bool(d % 7 in (5, 6)), "month": 1 + (d // 30) % 12, "storm": np.zeros(lanes, bool)}
            for d in range(days)]


def test_grid4h_registered_with_labelled_values():
    spec = Spec("grid4h")
    assert spec.E == 32 and spec.R == 0 and spec.rte == 0.85 and spec.deg == 20.0
    assert spec.cap == 128 and spec.S == 33 and abs(spec.usable_mwh - 4.0) < 1e-12
    assert specs.hours("grid4h") == 4.0 and specs.hours("grid") == 2.0
    # the arena's own 2-hour spec is untouched
    g = C.SPECS["grid"]
    assert (g["energy_units"], g["reserve_units"], g["rte"], g["cycles_per_day"], g["deg_usd_mwh"]) == (16, 0, 0.85, 4.0, 20.0)
    assert set(specs.GRID4H_PROVENANCE) == {"energy_units", "reserve_units", "rte", "cycles_per_day", "deg_usd_mwh", "label"}
    assert all(lab in ("REAL", "DERIVED", "SIM", "ASSUMPTION") for _, lab, _ in specs.GRID4H_PROVENANCE.values())
    # idempotent and refuses to silently overwrite a different entry
    specs.register()
    specs.register()
    C.SPECS["grid4h"]["rte"] = 0.9
    try:
        specs.register()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on conflicting entry")
    finally:
        C.SPECS["grid4h"]["rte"] = 0.85


def test_oracle_days_with_grid4h_closed_form():
    """Four cheap hours at $10, four dear hours at $110, the rest at $60: the 4 MWh battery earns exactly twice
    the 2 MWh battery (no cycle cap binds), and both match the hand calculation."""
    P = np.full((1, 96), 60.0)
    P[0, 16:32] = 10.0        # 04:00-08:00
    P[0, 64:80] = 110.0       # 16:00-20:00
    p4 = oracle_days(P, Spec("grid4h"))[0]
    p2 = oracle_days(P, Spec("grid"))[0]
    rte, deg = 0.85, 20.0
    per_mwh = 110.0 - deg - 10.0 / rte
    assert abs(p2 - 2.0 * per_mwh) < 1e-9, (p2, 2.0 * per_mwh)
    assert abs(p4 - 4.0 * per_mwh) < 1e-9, (p4, 4.0 * per_mwh)


def test_oracle_days_grid4h_vs_grid_on_random_days():
    P = _synthetic_prices(40, 1, 3)[:, 0, :]
    o2 = oracle_days(P, Spec("grid"))
    o4 = oracle_days(P, Spec("grid4h"))
    assert o4.shape == (40,) and (o4 >= o2 - 1e-9).all()
    assert (o4 > o2 + 1e-9).any()
    assert o4.sum() < 2.0 * o2.sum() + 1e-6          # a second pair of hours is worth less than the first


def test_run_combined_with_grid4h():
    D, L = 14, 3
    P = _synthetic_prices(D + 10, L, 11)
    sp = Spec("grid4h")
    warm = P[:10]
    days = P[10:]
    hf = lambda: History(warm, np.zeros(10, bool))  # noqa: E731
    leds, picks = run_combined(sp, hf, days, _ctx(D, L, sp))
    assert set(leds) == {"A1", "A2", "A3", "A4", "A5"} and picks.shape == (D, L)
    for k, led in leds.items():
        for arr in (led.energy, led.wear, led.mwh_out, led.backup, led.profit):
            assert arr.shape == (D, L) and np.isfinite(arr).all(), k
        assert np.allclose(led.wear, 20.0 * led.mwh_out), k                       # wear = $20 per MWh discharged
        assert (led.mwh_out <= sp.usable_mwh * 4.0 + 1e-9).all(), k               # at most 4 full cycles a day
        assert (led.backup <= sp.E * C.U + 1e-9).all() and (led.backup >= 0).all(), k
    assert sum(float(led.mwh_out.sum()) for led in leds.values()) > 0               # somebody traded
    oracle = np.stack([oracle_days(days[:, l, :], sp) for l in range(L)], 1)
    for k, led in leds.items():
        assert (led.profit.sum() <= oracle.sum() + 1e-6), (k, led.profit.sum(), oracle.sum())


def test_run_combined_grid4h_and_grid_use_different_storage():
    D, L = 10, 2
    P = _synthetic_prices(D + 10, L, 5)
    warm, days = P[:10], P[10:]
    out = {}
    for key in ("grid", "grid4h"):
        sp = Spec(key)
        leds, _ = run_combined(sp, lambda: History(warm, np.zeros(10, bool)), days, _ctx(D, L, sp))
        out[key] = leds
    # A1 sells in a block of (E - R) // 2 intervals: 8 for the 2 h battery, 16 for the 4 h battery
    assert out["grid4h"]["A1"].mwh_out.max() > out["grid"]["A1"].mwh_out.max()
    assert out["grid"]["A1"].mwh_out.max() <= 2.0 * 4 + 1e-9


if __name__ == "__main__":
    H.run_module(globals())
